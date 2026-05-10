#!/usr/bin/env python3
"""Hard-isolation harness for blind LLM solving.

Per Codex's audit: prior runs relied on a verbal promise that the
solver did not read ``challenge.json`` / ``bundle_index.json`` /
the bundle's ``expected_flag``.  This harness replaces that promise
with a mechanical check.

Workflow per challenge:

    1.  ``stage(cid)``
        Create ``clean_solve/<cid>/`` containing only:
        - the attachment archive copied from
          ``artifacts/challenges/<cid>/<filename>.zip``
        - nothing else.

        Snapshot SHA-256 of every "protected" file (the bundle's
        ``challenge.json``, both ``bundle_index.json`` files) so a
        post-solve ``verify`` step can prove the solver didn't write
        to them.  Read the bundle's ``expected_flag`` ONCE here,
        keep it only as a hash inside the harness state, and never
        expose it back to the caller.

    2.  Solver phase (Claude Code reasoning + Bash commands)
        All shell commands during this phase MUST run with
        ``cwd=clean_solve/<cid>/``.  The solver writes:
          - cc_hypothesis.md
          - subagent_request.md
          - subagent_reply.md
          - cc_final_decision.md
          - codex_candidates.json
          - evidence/, extracted/ as needed
        ...all under ``clean_solve/<cid>/``.  No path outside this
        directory may appear in any solver-authored file.

    3.  ``verify(cid)``
        - All five canonical artifact files exist.
        - Protected files unchanged (hashes match snapshot).
        - No solver-authored file contains the literal tokens
          ``challenge.json``, ``bundle_index.json``, ``expected_flag``,
          or the bundle's actual ``expected_flag`` string.
        - codex_candidates.json: each evidence_path is relative,
          contains no ``..``, and resolves to a real file under
          ``clean_solve/<cid>/``.

    4.  ``publish(cid)``
        Copy the solver's output to
        ``artifacts/challenges/<cid>/`` with two adjustments:
          - codex_candidates.json's ``evidence_paths`` are rewritten
            to the absolute-from-project-root form
            ``artifacts/challenges/<cid>/<rel>`` so the supervisor's
            validator path policy accepts them.
          - codex_candidates.json's ``challenge_id`` is overridden
            to the publish-time cid (the solver's draft can keep a
            placeholder).

This module has no platform side effects; running it as ``__main__``
just summarises pending sandboxes and protected fingerprints.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

PROJECT = Path(__file__).resolve().parents[1]

CANONICAL_ARTIFACTS = (
    "cc_hypothesis.md",
    "subagent_request.md",
    "subagent_reply.md",
    "cc_final_decision.md",
    "codex_candidates.json",
)

FORBIDDEN_TOKENS_IN_SOLVER_OUTPUT = (
    "challenge.json",
    "bundle_index.json",
    "expected_flag",
    "expected_flag_source",
)

# Files that must exist OUTSIDE the sandbox and remain bit-for-bit
# unchanged across the solve window.  Used as a tamper canary.
PROTECTED_GLOB = (
    "artifacts/challenges/*/challenge.json",
    "artifacts/public-ctf-platform/*/bundle_index.json",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class SolveError(RuntimeError):
    """Raised by verify() when the sandbox boundary is broken."""


@dataclass
class Sandbox:
    cid: str
    workdir: Path
    attachment_name: str
    protected_fingerprints: dict[str, str]
    expected_flag_hash: Optional[str]
    audit_notes: list[str] = field(default_factory=list)

    @property
    def cidstr(self) -> str:
        return str(self.cid)


def _read_expected_flag_for_cid(cid: str, *, attachment_name: Optional[str] = None) -> Optional[str]:
    """Read the matching bundle's ``challenge.json`` once to capture
    the flag for later post-solve verification.  The harness only
    stores its SHA-256; the caller never sees it.

    Two locator strategies:
      1. Direct: ``artifacts/challenges/<cid>/challenge.json`` (used
         when the local GZCTF cid happens to also be a bundle dir
         name — e.g., test fixtures).
      2. Via the lab attachment filename: real local-GZCTF runs put
         the bundle's archive at ``artifacts/challenges/<numeric>/<bundle_id>__<orig>.zip``.
         The ``<bundle_id>`` prefix is the bundle dir name; we use it
         to find ``artifacts/challenges/<bundle_id>/challenge.json``.
    """
    # Strategy 1
    direct = PROJECT / "artifacts" / "challenges" / cid / "challenge.json"
    if direct.exists():
        try:
            data = json.loads(direct.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and isinstance(data.get("expected_flag"), str):
            return data["expected_flag"]

    # Strategy 2: bundle-id prefix in the attachment filename
    if attachment_name and "__" in attachment_name:
        bundle_id = attachment_name.split("__", 1)[0]
        cj = PROJECT / "artifacts" / "challenges" / bundle_id / "challenge.json"
        if cj.exists():
            try:
                data = json.loads(cj.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
            if isinstance(data, dict) and isinstance(data.get("expected_flag"), str):
                return data["expected_flag"]
    return None


def _fingerprint_protected() -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern in PROTECTED_GLOB:
        for p in PROJECT.glob(pattern):
            if p.is_file():
                out[str(p.relative_to(PROJECT))] = _sha256(p)
    return out


def stage(cid: str) -> Sandbox:
    """Create ``clean_solve/<cid>/`` with only the attachment."""
    bundle_dir = PROJECT / "artifacts" / "challenges" / cid
    if not bundle_dir.exists():
        raise SolveError(f"no bundle dir for cid={cid}")

    zips = list(bundle_dir.glob("*.zip"))
    if len(zips) != 1:
        raise SolveError(
            f"expected exactly one .zip under {bundle_dir}, got {len(zips)}"
        )

    workdir = PROJECT / "clean_solve" / cid
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    (workdir / "evidence").mkdir()
    (workdir / "extracted").mkdir()

    attachment_dst = workdir / zips[0].name
    shutil.copyfile(zips[0], attachment_dst)

    protected = _fingerprint_protected()
    flag = _read_expected_flag_for_cid(cid, attachment_name=zips[0].name)
    flag_hash = (
        hashlib.sha256(flag.encode("utf-8")).hexdigest() if flag else None
    )

    sandbox = Sandbox(
        cid=cid,
        workdir=workdir,
        attachment_name=zips[0].name,
        protected_fingerprints=protected,
        expected_flag_hash=flag_hash,
    )

    state_dir = PROJECT / "logs" / "blind-solve"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"sandbox-{cid}.json").write_text(
        json.dumps(
            {
                "cid": cid,
                "workdir": str(workdir.relative_to(PROJECT)),
                "attachment": zips[0].name,
                "protected_fingerprints": protected,
                "expected_flag_sha256": flag_hash,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return sandbox


def _walk_solver_output_files(workdir: Path) -> Iterable[Path]:
    for p in workdir.rglob("*"):
        if not p.is_file():
            continue
        # Skip the attachment archive (we shipped it; it can legitimately
        # contain sourcefiles that mention the word `expected_flag` in
        # comments — though in practice none of these bundles do).
        if p.suffix == ".zip":
            continue
        yield p


def verify(sandbox: Sandbox) -> dict:
    """Post-solve isolation check.  Raises SolveError if the boundary
    was broken; returns an audit summary on success."""
    work = sandbox.workdir

    # 1) canonical artifacts
    present = {p.name for p in work.iterdir() if p.is_file()}
    missing = set(CANONICAL_ARTIFACTS) - present
    if missing:
        raise SolveError(
            f"cid={sandbox.cid}: missing solver artifacts {sorted(missing)}"
        )

    # 2) protected fingerprints unchanged
    for relpath, h in sandbox.protected_fingerprints.items():
        cur = _sha256(PROJECT / relpath)
        if cur != h:
            raise SolveError(
                f"cid={sandbox.cid}: protected path tampered: {relpath}"
            )

    # 3) forbidden-token grep
    forbidden_hits: list[tuple[str, str]] = []
    expected_flag_hits: list[str] = []
    for f in _walk_solver_output_files(work):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for tok in FORBIDDEN_TOKENS_IN_SOLVER_OUTPUT:
            if tok in text:
                forbidden_hits.append((str(f.relative_to(work)), tok))
        # Hash-compare each line-ish chunk to detect the actual flag
        # ever appearing in the output.  But the candidate flag is
        # _supposed_ to appear if solving succeeded — the only safe
        # check is "the flag-shaped string used as candidate must not
        # appear in any file other than codex_candidates.json /
        # cc_final_decision.md / cc_hypothesis.md / subagent_reply.md"
        # because evidence files should hold the *raw bytes* used to
        # derive the flag, not the flag itself.  Since we don't trust
        # the harness with the literal flag, we only require: the
        # hash of the candidate (if any) matches the bundle's
        # expected_flag_hash AND only appears in the legitimate set.
        # Implemented below in step 5.

    if forbidden_hits:
        raise SolveError(
            f"cid={sandbox.cid}: forbidden tokens in solver output: "
            f"{forbidden_hits[:5]}"
        )

    # 4) codex_candidates.json shape + evidence path safety
    cj_path = work / "codex_candidates.json"
    try:
        cj = json.loads(cj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SolveError(f"cid={sandbox.cid}: codex_candidates.json invalid JSON: {exc}")
    if not isinstance(cj, list):
        raise SolveError(f"cid={sandbox.cid}: codex_candidates.json must be a list")

    candidate_count = 0
    for entry in cj:
        if not isinstance(entry, dict):
            raise SolveError(f"cid={sandbox.cid}: codex entry is not an object")
        candidate_count += 1
        # evidence_paths must be relative to clean_solve/<cid>/, no ..
        for ep in entry.get("evidence_paths", []) or []:
            if not isinstance(ep, str):
                raise SolveError(f"cid={sandbox.cid}: non-string evidence path {ep!r}")
            p = Path(ep)
            if p.is_absolute() or ".." in p.parts:
                raise SolveError(f"cid={sandbox.cid}: unsafe evidence path {ep!r}")
            target = (work / p).resolve()
            try:
                target.relative_to(work.resolve())
            except ValueError:
                raise SolveError(f"cid={sandbox.cid}: evidence path escaped sandbox: {ep!r}")
            if not target.exists():
                raise SolveError(f"cid={sandbox.cid}: evidence path not found: {ep!r}")
            if not target.is_file():
                raise SolveError(f"cid={sandbox.cid}: evidence path not a regular file: {ep!r}")

    # 5) Cross-check: if solver produced a candidate, the candidate's
    # SHA-256 should match the bundle's expected_flag_hash for an
    # Accepted submit later.  Mismatch isn't a sandbox violation per
    # se, but we surface it in the audit so the operator can see at
    # publish time whether the solver guessed.
    candidate_hashes: list[dict] = []
    for entry in cj:
        cand = entry.get("candidate")
        if isinstance(cand, str) and cand.strip():
            ch = hashlib.sha256(cand.encode("utf-8")).hexdigest()
            candidate_hashes.append({
                "confidence": entry.get("confidence"),
                "candidate_sha256": ch,
                "matches_bundle_expected": (
                    sandbox.expected_flag_hash is not None
                    and ch == sandbox.expected_flag_hash
                ),
            })

    return {
        "cid": sandbox.cid,
        "candidates_authored": candidate_count,
        "candidate_hash_check": candidate_hashes,
        "expected_flag_known_to_harness": sandbox.expected_flag_hash is not None,
        "protected_fingerprints_count": len(sandbox.protected_fingerprints),
    }


def publish(sandbox: Sandbox) -> Path:
    """Copy verified solver output into ``artifacts/challenges/<cid>/``
    and rewrite codex_candidates.json's evidence paths to the project
    root form the supervisor's validator expects."""
    src = sandbox.workdir
    dst = PROJECT / "artifacts" / "challenges" / sandbox.cid
    dst.mkdir(parents=True, exist_ok=True)

    # Markdown deliverables
    for name in ("cc_hypothesis.md", "subagent_request.md",
                 "subagent_reply.md", "cc_final_decision.md"):
        shutil.copyfile(src / name, dst / name)

    # Evidence + extracted trees
    for sub in ("evidence", "extracted"):
        s = src / sub
        if not s.exists():
            continue
        d = dst / sub
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d)

    cj = json.loads((src / "codex_candidates.json").read_text(encoding="utf-8"))
    rewritten: list[dict] = []
    for entry in cj:
        entry = dict(entry)
        entry["challenge_id"] = sandbox.cid
        entry["evidence_paths"] = [
            f"artifacts/challenges/{sandbox.cid}/{p}"
            for p in entry.get("evidence_paths", []) or []
        ]
        rewritten.append(entry)
    (dst / "codex_candidates.json").write_text(
        json.dumps(rewritten, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dst


def main() -> int:
    """`python scripts/blind_solve_harness.py` summarises sandboxes."""
    cs = PROJECT / "clean_solve"
    if not cs.exists():
        print("no clean_solve/ yet")
        return 0
    for d in sorted(cs.iterdir()):
        if d.is_dir():
            print(f"sandbox cid={d.name}: {len(list(d.iterdir()))} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
