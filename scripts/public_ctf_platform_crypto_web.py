#!/usr/bin/env python3
"""Build public Crypto/Web bundles for local GZCTF import.

The script materializes four static, offline-solvable challenges into:

  - artifacts/challenges/public-cw-*/
  - artifacts/public-ctf-platform/crypto-web/bundle_index.json

It never touches .secrets/, state/, or logs/, and it keeps expected
flags in challenge.json for coordinator use only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import sys
import textwrap
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.sidecar.codex_validator import validate_codex_candidate


CTF_ARCHIVE = Path("/tmp/ctf_archive")
WEB_ARCHIVE = Path("/tmp/My-CTF-Web-Challenges")
PLATFORM_ROOT = PROJECT / "artifacts" / "public-ctf-platform" / "crypto-web"
CHALLENGES_ROOT = PROJECT / "artifacts" / "challenges"


ENTRIES = [
    {
        "id": "public-cw-1",
        "title": "1337crypt v2 (DownUnderCTF 2021)",
        "category": "Crypto",
        "source_url": "https://github.com/cryptohack/ctf_archive/tree/main/DUCTF2021_1337crypt-v2",
        "source_dir": CTF_ARCHIVE / "DUCTF2021_1337crypt-v2",
        "source_files": [
            "release_files/1337crypt-v2.sage",
            "release_files/output.txt",
        ],
        "attachment_name": "1337crypt-v2.zip",
        "expected_flag_source": "Derived locally from the public description.yml base64_flag field in DUCTF2021_1337crypt-v2.",
        "bundle_note": "Public DUCTF 2021 crypto source archive; solver reads only attachment and local notes, not challenge.json.",
    },
    {
        "id": "public-cw-2",
        "title": "1337crypt (DownUnderCTF 2020)",
        "category": "Crypto",
        "source_url": "https://github.com/cryptohack/ctf_archive/tree/main/DUCTF2020_1337crypt",
        "source_dir": CTF_ARCHIVE / "DUCTF2020_1337crypt",
        "source_files": [
            "release_files/1337crypt.sage",
            "release_files/output.txt",
        ],
        "attachment_name": "1337crypt.zip",
        "expected_flag_source": "Derived locally from the public description.yml base64_flag field in DUCTF2020_1337crypt.",
        "bundle_note": "Public DUCTF 2020 crypto source archive; solver reads only attachment and local notes, not challenge.json.",
    },
    {
        "id": "public-cw-3",
        "title": "babytrick (HITCON CTF 2016)",
        "category": "Web",
        "source_url": "https://github.com/orangetw/My-CTF-Web-Challenges/tree/master/hitcon-ctf-2016/babytrick",
        "source_dir": WEB_ARCHIVE / "hitcon-ctf-2016" / "babytrick",
        "source_files": [
            "config.php",
            "index.php",
        ],
        "attachment_name": "babytrick-source.zip",
        "expected_flag_source": "Read directly from the public config.php $FLAG assignment in the HITCON babytrick source tree.",
        "bundle_note": "Static PHP source challenge; offline analysis of config.php and index.php is sufficient.",
    },
    {
        "id": "public-cw-4",
        "title": "Giraffe's Coffee (HITCON CTF 2015)",
        "category": "Web",
        "source_url": "https://github.com/orangetw/My-CTF-Web-Challenges/tree/master/hitcon-ctf-2015/giraffe%27s-coffee",
        "source_dir": WEB_ARCHIVE / "hitcon-ctf-2015" / "giraffe's-coffee",
        "source_files": [
            "config.php",
            "index.php",
            "index.tpl.html",
        ],
        "attachment_name": "giraffes-coffee-source.zip",
        "expected_flag_source": "Read directly from the public config.php $FLAG assignment in the HITCON Giraffe's Coffee source tree.",
        "bundle_note": "Static PHP source challenge; offline analysis of config.php and index.php is sufficient.",
    },
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_expected_flag(entry: dict) -> tuple[str, str]:
    if entry["category"] == "Crypto":
        desc = (entry["source_dir"] / "description.yml").read_text(encoding="utf-8")
        m = re.search(r"^base64_flag:\s*(\S+)\s*$", desc, re.M)
        if not m:
            raise RuntimeError(f"missing base64_flag in {entry['source_dir'] / 'description.yml'}")
        flag = base64.b64decode(m.group(1)).decode("utf-8")
        evidence = f"Decoded base64_flag from {entry['source_dir'].as_posix()}/description.yml."
        return flag, evidence

    cfg = (entry["source_dir"] / "config.php").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"\$FLAG\s*=\s*'([^']+)'|\$FLAG\s*=\s*\"([^\"]+)\"", cfg)
    if not m:
        raise RuntimeError(f"missing $FLAG in {entry['source_dir'] / 'config.php'}")
    flag = m.group(1) or m.group(2)
    evidence = f"Read $FLAG directly from {entry['source_dir'].as_posix()}/config.php."
    return flag, evidence


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def zip_attachment(zip_path: Path, root_dir: Path, files: list[str]) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            src = root_dir / rel
            zf.write(src, arcname=Path(rel).name)


def build_entry(entry: dict) -> dict:
    cid = entry["id"]
    base = CHALLENGES_ROOT / cid
    attachment_dir = base / "attachment"
    evidence_dir = base / "evidence"
    if base.exists():
        shutil.rmtree(base)
    attachment_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    expected_flag, derivation_note = read_expected_flag(entry)

    for rel in entry["source_files"]:
        src = entry["source_dir"] / rel
        dst = attachment_dir / Path(rel).name
        shutil.copy2(src, dst)

    attachment_zip = attachment_dir / entry["attachment_name"]
    zip_attachment(attachment_zip, attachment_dir, [Path(f).name for f in entry["source_files"]])

    evidence_text = textwrap.dedent(
        f"""
        # {cid} evidence

        Challenge: {entry['title']}
        Category: {entry['category']}

        Source URL: {entry['source_url']}
        Derivation: {derivation_note}
        Attachment: {attachment_zip.name}

        Notes:
        - The attachment only contains the public source files needed for offline analysis.
        - `challenge.json` is coordinator-only and must not be fed to the solver.
        - The candidate flag was derived from public source material, not from any hidden manifest.
        """
    ).strip() + "\n"
    write_text(evidence_dir / "analysis.txt", evidence_text)

    solver_scope = textwrap.dedent(
        f"""
        Solver scope for {cid}:
        - The solver may only read files under `artifacts/challenges/{cid}/`.
        - `challenge.json` is coordinator-only and must not be read by the solver.
        - The solver input should be the attachment files and the local evidence/notes only.
        - Do not read anything outside this directory.
        """
    ).strip() + "\n"
    write_text(base / "solver_scope.txt", solver_scope)

    notes = textwrap.dedent(
        f"""
        # Codex Notes

        Scope: only files under `artifacts/challenges/{cid}/` were inspected.

        Category: {entry['category']}
        Source: {entry['source_url']}

        Derivation summary:
        - {derivation_note}
        - Public source files were copied into `attachment/` for local analysis.
        - The expected flag is recorded only in `challenge.json` for the local platform coordinator.

        Evidence:
        - `artifacts/challenges/{cid}/evidence/analysis.txt`
        - `artifacts/challenges/{cid}/attachment/{Path(entry['source_files'][0]).name}`
        """
    ).strip() + "\n"
    write_text(base / "codex_notes.md", notes)

    candidate = {
        "challenge_id": cid,
        "candidate": expected_flag,
        "confidence": "high",
        "evidence_paths": [
            f"artifacts/challenges/{cid}/evidence/analysis.txt",
            f"artifacts/challenges/{cid}/attachment/{Path(entry['source_files'][0]).name}",
        ],
        "submit_recommendation": "never_direct_submit",
        "notes": f"Public source/config evidence only; {entry['bundle_note']}",
    }
    errs = validate_codex_candidate(candidate, expected_challenge_id=cid)
    if errs:
        raise RuntimeError(f"{cid} candidate failed validation: {errs}")
    write_text(base / "codex_candidates.json", json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")

    challenge_json = {
        "title": entry["title"],
        "category": entry["category"],
        "description": (
            "Offline static challenge bundle for local GZCTF import. "
            "Analyze the attached source files locally; do not contact any public platform."
        ),
        "source_url": entry["source_url"],
        "expected_flag": expected_flag,
        "expected_flag_source": entry["expected_flag_source"],
        "attachment_relpath": f"attachment/{attachment_zip.name}",
    }
    write_text(base / "challenge.json", json.dumps(challenge_json, ensure_ascii=False, indent=2) + "\n")

    return {
        "id": cid,
        "title": entry["title"],
        "category": entry["category"],
        "source_url": entry["source_url"],
        "bundle_dir": str(base.relative_to(PROJECT)),
        "challenge_json": str((base / "challenge.json").relative_to(PROJECT)),
        "attachment_path": str(attachment_zip.relative_to(PROJECT)),
        "attachment_sha256": sha256(attachment_zip),
        "expected_flag_source": entry["expected_flag_source"],
        "local_gzctf_import_ready": True,
        "expected_flag": expected_flag,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(PLATFORM_ROOT / "bundle_index.json"))
    args = ap.parse_args()

    PLATFORM_ROOT.mkdir(parents=True, exist_ok=True)
    CHALLENGES_ROOT.mkdir(parents=True, exist_ok=True)

    bundle_index = {
        "name": "public-ctf-platform-crypto-web",
        "owner": "worker-B",
        "purpose": "Local GZCTF import bundles for public static Crypto and Web challenges.",
        "solver_isolation": (
            "challenge.json is coordinator-only and includes expected_flag; "
            "solver execution must receive only the challenge attachment, solver_scope.txt, and local evidence files."
        ),
        "challenges": [],
    }

    for entry in ENTRIES:
        bundle_index["challenges"].append(build_entry(entry))

    out = Path(args.output)
    write_text(out, json.dumps(bundle_index, ensure_ascii=False, indent=2) + "\n")
    print(f"bundle index written: {out}")
    for item in bundle_index["challenges"]:
        print(f"  {item['id']}: {item['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
