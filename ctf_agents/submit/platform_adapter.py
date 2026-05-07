from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
@dataclass
class SubmitResult:
    ok: bool; correct: bool | None; message: str; raw: dict | None = None
class PlatformAdapter(Protocol):
    def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult: ...
class DryRunAdapter:
    def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        return SubmitResult(ok=True, correct=None, message=f"DRYRUN: would submit {flag} to {challenge_id}", raw={})
