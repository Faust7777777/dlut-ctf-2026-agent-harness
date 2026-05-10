"""Codex sidecar artifact validator.

The supervisor's contract with Codex (via ``codex-plugin-cc``) is
hard-edged: Codex is a P2 sidecar that may produce notes / candidate
JSON / patches under ``artifacts/challenges/<id>/``, but it must NEVER

  - read ``.env`` / ``.secrets/`` / ``logs/submission_state.json``
  - mutate ``state/ai_contest_state.json``
  - call GZCTF directly
  - submit a flag

Every Codex output is parsed by this validator before the supervisor
considers it.  If validation fails, the candidate is dropped (logged
as ``codex_candidate_rejected`` in the runtime log) and the
supervisor's deterministic state machine carries on without it.

This module has zero network or platform side-effects — it's pure
schema + path policy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


REQUIRED_KEYS = {
    "challenge_id",
    "candidate",
    "confidence",
    "evidence_paths",
    "submit_recommendation",
    "notes",
}

ALLOWED_CONFIDENCES = {"low", "medium", "high"}

# Codex outputs must NOT contain these keys.  They would imply Codex
# is trying to bypass guard / supervisor / scope.
FORBIDDEN_PAYLOAD_KEYS = {
    "submit",
    "platform_call",
    "secret",
    "cookie",
    "password",
    "api_key",
    "token",
    "force_submit",
    "bypass_guard",
    "auth",
}

# Path tokens that indicate the candidate is trying to reach into
# forbidden parts of the project (secrets, runtime state, dotenv).
PATH_BLOCKLIST_TOKENS = (
    ".env",
    ".secrets",
    "submission_state.json",
    "ai_contest_state.json",
    "state/ai_contest_state",
    "state/submission_state",
    "/passwd",
    "/etc/",
    "id_rsa",
    "private_key",
)


def _expected_path_prefix(challenge_id: Optional[str]) -> str:
    """Strict prefix every evidence_path must start with.

    When ``expected_challenge_id`` is supplied (the supervisor always
    supplies it), the prefix is fully pinned so a malicious / drifted
    Codex output can't reference *another* challenge's artifacts —
    only its own.  When omitted (offline tooling), we still require
    ``artifacts/challenges/`` so paths like ``tmp/artifacts/x`` no
    longer slip through.
    """
    if challenge_id:
        return f"artifacts/challenges/{challenge_id}/"
    return "artifacts/challenges/"


def validate_codex_candidate(
    payload: Any,
    *,
    expected_challenge_id: Optional[str] = None,
) -> list[str]:
    """Return a list of human-readable errors.  Empty list = valid.

    The validator is deliberately strict: any whiff of a forbidden
    field or out-of-sandbox path fails the whole candidate.  Codex is
    never given the benefit of the doubt.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"payload is not an object (got {type(payload).__name__})"]

    missing = REQUIRED_KEYS - set(payload.keys())
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")

    if expected_challenge_id is not None:
        cid = str(payload.get("challenge_id", ""))
        if cid != str(expected_challenge_id):
            errors.append(
                f"challenge_id mismatch: payload={cid!r} expected={str(expected_challenge_id)!r}"
            )

    confidence = payload.get("confidence")
    if confidence not in ALLOWED_CONFIDENCES:
        errors.append(
            f"confidence must be one of {sorted(ALLOWED_CONFIDENCES)}, got {confidence!r}"
        )

    rec = payload.get("submit_recommendation")
    if rec != "never_direct_submit":
        errors.append(
            f"submit_recommendation must be 'never_direct_submit' (got {rec!r}); "
            "Codex outputs always pass through FlagGuard"
        )

    candidate = payload.get("candidate")
    if not isinstance(candidate, str) or not candidate.strip():
        errors.append("candidate must be a non-empty string")

    evidence = payload.get("evidence_paths")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence_paths must be a non-empty list")
    else:
        cid_for_path = (
            str(expected_challenge_id)
            if expected_challenge_id is not None
            else (str(payload.get("challenge_id")) if payload.get("challenge_id") else None)
        )
        for raw in evidence:
            if not isinstance(raw, str):
                errors.append(f"evidence_path entry not a string: {raw!r}")
                continue
            errors.extend(_check_evidence_path(raw, expected_challenge_id=cid_for_path))

    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        errors.append(f"notes must be a string or null (got {type(notes).__name__})")

    forbidden_present = FORBIDDEN_PAYLOAD_KEYS & set(payload.keys())
    if forbidden_present:
        errors.append(
            f"payload contains forbidden keys: {sorted(forbidden_present)} — "
            "Codex cannot pass auth/submit/secret hints to the supervisor"
        )

    return errors


def _check_evidence_path(
    p: str,
    *,
    expected_challenge_id: Optional[str] = None,
) -> list[str]:
    errs: list[str] = []
    if not p.strip():
        errs.append("evidence_path is empty")
        return errs
    if p.startswith("/"):
        errs.append(f"evidence_path is absolute (forbidden): {p}")
    if ".." in Path(p).parts:
        errs.append(f"evidence_path uses '..' (forbidden): {p}")
    lower = p.lower()
    for tok in PATH_BLOCKLIST_TOKENS:
        if tok in lower:
            errs.append(f"evidence_path references blocked location ({tok!r}): {p}")
            break
    # Strict structural prefix.  Codex outputs must reference only files
    # inside this challenge's own subdir; ``tmp/artifacts/x``,
    # ``artifacts/foo/x`` and references to a sibling challenge id are
    # all rejected.
    required_prefix = _expected_path_prefix(expected_challenge_id)
    if not p.startswith(required_prefix):
        errs.append(
            f"evidence_path must start with {required_prefix} (got {p})"
        )
    return errs


def is_safe_artifact_path(path: str | Path, project_root: str | Path) -> bool:
    """True iff ``path`` is inside ``<project_root>/artifacts/`` after
    resolution.  Used by the supervisor's ingest step to refuse any
    Codex artifact that tries to escape the sandbox via symlinks or
    parent traversal.
    """
    project = Path(project_root).resolve()
    artifacts_root = (project / "artifacts").resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (project / p).resolve()
    else:
        p = p.resolve()
    try:
        p.relative_to(artifacts_root)
    except ValueError:
        return False
    return True


def check_sandbox_filesystem(project_root: str | Path) -> dict:
    """Cheap pre-flight that confirms the Codex sidecar's sandbox
    structure exists and forbidden paths exist OUT of bounds.  Used by
    the dry-run script before pretending to invoke Codex.
    """
    project = Path(project_root).resolve()
    artifacts = project / "artifacts"
    secrets = project / ".secrets"
    state_dir = project / "state"

    return {
        "artifacts_dir_present": artifacts.exists() and artifacts.is_dir(),
        "secrets_dir_present": secrets.exists() and secrets.is_dir(),
        "state_dir_present": state_dir.exists() and state_dir.is_dir(),
        "artifacts_path": str(artifacts),
        "out_of_bounds": {
            ".env": str(project / ".env"),
            ".secrets": str(secrets),
            "state/ai_contest_state.json": str(state_dir / "ai_contest_state.json"),
        },
    }


__all__ = [
    "REQUIRED_KEYS",
    "ALLOWED_CONFIDENCES",
    "FORBIDDEN_PAYLOAD_KEYS",
    "PATH_BLOCKLIST_TOKENS",
    "validate_codex_candidate",
    "is_safe_artifact_path",
    "check_sandbox_filesystem",
]
