"""Real Misc-category agent.

Sequentially applies common forensic techniques to a Misc CTF
attachment.  At each stage that yields a flag-pattern match, returns a
FlagCandidate with evidence count proportional to how many independent
techniques converged.  When no technique finds a flag, returns None
(routes to no_candidate → human review).

Techniques (in order):
  1. file-type detection (header magic), to catch ``.zip`` actually
     being PNG and similar misdirection
  2. recursive unzip / unrar of archive types
  3. ``strings`` over every reachable file, with flag-pattern grep
  4. ``binwalk`` extraction for embedded files
  5. ``exiftool`` metadata
  6. ``zsteg`` LSB stego on PNG/BMP files

Agent returns:
  - FlagCandidate with high confidence if a single canonical flag is
    found via multiple techniques
  - FlagCandidate with low confidence if only one weak source
  - None if nothing matches → workflow records ``no_candidate``
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from ctf_agents.skill.router import Challenge
from ctf_agents.submit.flag_guard import FlagCandidate


FLAG_PATTERN = re.compile(
    r"(?i)(?:flag|bjd|hctf|dlutctf|dasctf|nss|moectf)\{[^{}\s]{3,200}\}"
)


def _try_run(cmd: list[str], **kwargs) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="ignore",
            timeout=kwargs.get("timeout", 30),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]} timed out"


def _grep_flag(text: str, source_label: str) -> list[tuple[str, str]]:
    if not text:
        return []
    return [(m.group(0), source_label) for m in FLAG_PATTERN.finditer(text)]


def _walk_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for p in root.rglob("*"):
        if p.is_file() and "__MACOSX" not in str(p):
            yield p


def _detect_real_type(p: Path) -> str:
    rc, out, _ = _try_run(["file", "-b", str(p)])
    return out.strip() if rc == 0 else "unknown"


def _try_unpack(p: Path, dest: Path) -> bool:
    """Best-effort unpack into dest. Returns True if something was extracted."""
    real_type = _detect_real_type(p).lower()
    dest.mkdir(parents=True, exist_ok=True)
    if "zip archive" in real_type or p.suffix.lower() == ".zip":
        rc, _, _ = _try_run(["unzip", "-o", "-q", str(p), "-d", str(dest)])
        return rc == 0
    if "rar archive" in real_type or p.suffix.lower() == ".rar":
        rc, _, _ = _try_run(["unrar", "x", "-o+", "-y", str(p), str(dest) + "/"])
        if rc == 0:
            return True
        rc, _, _ = _try_run(["7z", "x", str(p), f"-o{dest}", "-y"])
        return rc == 0
    if "7-zip" in real_type or p.suffix.lower() == ".7z":
        rc, _, _ = _try_run(["7z", "x", str(p), f"-o{dest}", "-y"])
        return rc == 0
    return False


def _strings_grep(p: Path) -> list[tuple[str, str]]:
    rc, out, _ = _try_run(["strings", "-a", str(p)])
    if rc != 0:
        return []
    return _grep_flag(out, f"strings:{p.name}")


def _binwalk_extract(p: Path, work: Path) -> list[Path]:
    extract_dir = work / f"binwalk_{p.stem}"
    extract_dir.mkdir(parents=True, exist_ok=True)
    rc, _, _ = _try_run(
        ["binwalk", "-e", "-q", "--directory", str(extract_dir), str(p)],
        timeout=60,
    )
    if rc != 0:
        return []
    extracted = [
        f for f in _walk_files(extract_dir) if f.is_file() and f.stat().st_size > 0
    ]
    return extracted


def _exiftool_grep(p: Path) -> list[tuple[str, str]]:
    rc, out, _ = _try_run(["exiftool", str(p)])
    if rc != 0:
        return []
    return _grep_flag(out, f"exiftool:{p.name}")


def _zsteg_grep(p: Path) -> list[tuple[str, str]]:
    real_type = _detect_real_type(p).lower()
    if "png" not in real_type and "bmp" not in real_type:
        return []
    rc, out, _ = _try_run(["zsteg", "-a", str(p)], timeout=60)
    if rc != 0:
        return []
    return _grep_flag(out, f"zsteg:{p.name}")


def _png_inner_text(p: Path) -> list[tuple[str, str]]:
    """Decode tEXt / zTXt / iTXt PNG chunks via PIL."""
    real_type = _detect_real_type(p).lower()
    if "png" not in real_type:
        return []
    try:
        from PIL import Image
        img = Image.open(p)
        text_dict = getattr(img, "text", None) or getattr(img, "info", {})
        joined = "\n".join(f"{k}={v}" for k, v in (text_dict or {}).items())
        return _grep_flag(joined, f"png_text:{p.name}")
    except Exception:
        return []


def _scan_workdir(work: Path) -> list[tuple[str, str]]:
    """Run all per-file techniques on every file under work."""
    findings: list[tuple[str, str]] = []
    for f in _walk_files(work):
        findings.extend(_strings_grep(f))
        findings.extend(_exiftool_grep(f))
        findings.extend(_zsteg_grep(f))
        findings.extend(_png_inner_text(f))
    return findings


def _aggregate(findings: list[tuple[str, str]]) -> tuple[Optional[str], int, list[str]]:
    """Return (best_flag, evidence_count, sources)."""
    if not findings:
        return None, 0, []
    counter = Counter(flag for flag, _ in findings)
    best_flag, best_count = counter.most_common(1)[0]
    sources = [src for flag, src in findings if flag == best_flag]
    return best_flag, best_count, sources


def real_misc_agent(challenge: Challenge) -> Optional[FlagCandidate]:
    """Apply forensic techniques to ``challenge.attachments`` and return a
    FlagCandidate if anything matches the flag pattern.

    The agent does not call any LLM — it's a deterministic pipeline of
    forensic CLI tools.  ``challenge.attachments`` should be a list of
    paths (absolute or relative to CWD) on local disk.
    """
    attachments = challenge.attachments or []
    if not attachments:
        return None

    with tempfile.TemporaryDirectory(prefix="misc_agent_") as tmp_root:
        work = Path(tmp_root)
        all_findings: list[tuple[str, str]] = []
        all_techniques: list[str] = []

        for raw_attachment in attachments:
            attachment = Path(raw_attachment)
            if not attachment.exists():
                all_techniques.append(f"missing:{raw_attachment}")
                continue

            attach_work = work / attachment.stem
            attach_work.mkdir(parents=True, exist_ok=True)
            staged = attach_work / attachment.name
            shutil.copy(attachment, staged)

            unpacked_dir = attach_work / "unpacked"
            unpacked = _try_unpack(staged, unpacked_dir)
            all_techniques.append(f"unpack:{attachment.name}={'ok' if unpacked else 'no'}")

            if unpacked:
                # Recurse into the extracted tree
                for inner in list(_walk_files(unpacked_dir)):
                    deeper = attach_work / f"unpacked_{inner.stem}"
                    if _try_unpack(inner, deeper):
                        all_techniques.append(f"unpack-nested:{inner.name}=ok")

                inner_findings = _scan_workdir(unpacked_dir)
                all_findings.extend(inner_findings)
                all_techniques.append(
                    f"scan:{attachment.name}/unpacked={len(inner_findings)} findings"
                )

            # Always scan the staged file itself too
            outer_findings = _scan_workdir(attach_work)
            all_findings.extend(outer_findings)
            all_techniques.append(
                f"scan:{attachment.name}={len(outer_findings)} findings"
            )

            for f in _walk_files(attach_work):
                bin_extracted = _binwalk_extract(f, attach_work)
                if bin_extracted:
                    bin_findings = _scan_workdir(bin_extracted[0].parent)
                    all_findings.extend(bin_findings)
                    all_techniques.append(
                        f"binwalk:{f.name}=+{len(bin_extracted)} files,"
                        f" {len(bin_findings)} findings"
                    )

        flag, evidence_count, sources = _aggregate(all_findings)
        unique_techniques = len(set(s.split(":", 1)[0] for s in sources))

    if not flag:
        return None

    extraction_confidence = min(1.0, 0.5 + 0.15 * unique_techniques)
    return FlagCandidate(
        challenge_id=challenge.id,
        flag=flag,
        category="misc",
        evidence_count=min(8, evidence_count),
        extraction_confidence=extraction_confidence,
        agent_votes=[flag] * min(3, evidence_count),
        risk="normal",
    )
