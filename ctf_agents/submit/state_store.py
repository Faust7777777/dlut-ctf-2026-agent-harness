"""Persistent submission state with file-locking and atomic writes.

Two race-class problems this module is responsible for:

1. **Concurrent decide → submit → record race.**  Two agents calling
   ``flag_guard.decide()`` simultaneously could both receive
   ``AUTO_SUBMIT`` because neither has yet called ``record_submit()``.
   Fix: ``try_claim_submit_slot()`` does the rate-limit check *and* the
   timestamp claim atomically under the file lock, so only one caller
   per window can win the slot.

2. **Cross-process clock confusion.**  An earlier draft of this module
   stored ``time.monotonic()`` values, which are only meaningful inside
   one process — after a restart, the new process's monotonic clock
   starts near zero and treats persisted values as "100 s in the
   future", blocking all submits.  Fix: timestamps are persisted as Unix
   wall-clock seconds (``time.time()``).  The 25 s / 90 s rate-limit
   windows are not sensitive to small NTP drift, and the contest is one
   continuous 4.5 h session in which WSL2 is not expected to suspend.

Disk layout::

    {
        "global_last_submit_unix": 1715335444.12,
        "global_last_submit_iso":  "2026-05-10T13:34:01+00:00",
        "challenges": {
            "<challenge_id>": {
                "wrong_count": 1,
                "frozen": false,
                "frozen_at_iso": null,
                "last_submit_unix": 1715335420.0,
                "last_submit_iso":  "...",
                "submits": [{"ts": "...", "correct": true|false|null,
                             "flag_redacted": "...", "force": bool}]
            }
        }
    }

The file lock (``state.json.lock`` next to the state file) is held
across the read+modify+write cycle, so agents in separate processes
serialise correctly.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_SUBMIT_HISTORY_LIMIT = 50


@dataclass
class RateLimitView:
    remaining_global_s: float
    remaining_per_challenge_s: float

    @property
    def globally_ok(self) -> bool:
        return self.remaining_global_s <= 0.0

    @property
    def per_challenge_ok(self) -> bool:
        return self.remaining_per_challenge_s <= 0.0


def _redact_flag(flag: str) -> str:
    if not flag:
        return ""
    if len(flag) <= 14:
        return flag[:6] + "…"
    return flag[:6] + "…" + flag[-4:]


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


@contextlib.contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _compute_remaining(now_unix: float, last_unix: float, window_s: float) -> float:
    if last_unix <= 0:
        return 0.0
    if last_unix > now_unix:
        # Wall clock went backwards (NTP step / WSL sleep).  Treat the
        # anchor as "now" so we still wait the full window — conservative,
        # avoids accidentally permitting a flood of submits.
        return float(window_s)
    return max(0.0, window_s - (now_unix - last_unix))


class SubmissionStateStore:
    def __init__(self, state_path: str | Path):
        self.state_path = Path(state_path)
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {
                "global_last_submit_unix": 0.0,
                "global_last_submit_iso": None,
                "challenges": {},
            }
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            backup = self.state_path.with_suffix(self.state_path.suffix + ".corrupt")
            try:
                self.state_path.rename(backup)
            except OSError:
                pass
            raise RuntimeError(
                f"state file unparseable: {self.state_path} (backed up to {backup}): {exc}"
            )
        payload.setdefault("global_last_submit_unix", 0.0)
        payload.setdefault("global_last_submit_iso", None)
        payload.setdefault("challenges", {})
        return payload

    def _save(self, payload: dict) -> None:
        _atomic_write_json(self.state_path, payload)

    @staticmethod
    def _empty_challenge() -> dict:
        return {
            "wrong_count": 0,
            "frozen": False,
            "frozen_at_iso": None,
            "last_submit_unix": 0.0,
            "last_submit_iso": None,
            "submits": [],
        }

    def get_challenge(self, challenge_id: str) -> dict:
        with _file_lock(self.lock_path):
            payload = self._load()
            return dict(payload["challenges"].get(challenge_id, self._empty_challenge()))

    def is_frozen(self, challenge_id: str) -> bool:
        return bool(self.get_challenge(challenge_id).get("frozen", False))

    def wrong_count(self, challenge_id: str) -> int:
        return int(self.get_challenge(challenge_id).get("wrong_count", 0))

    def rate_limit_view(
        self,
        challenge_id: str,
        global_window_s: float,
        per_challenge_window_s: float,
    ) -> RateLimitView:
        """Read-only inspection of remaining rate-limit windows.  Does NOT
        claim a slot; use ``try_claim_submit_slot`` if the caller intends
        to submit."""
        with _file_lock(self.lock_path):
            payload = self._load()
            now_unix = time.time()
            last_global = float(payload.get("global_last_submit_unix", 0.0) or 0.0)
            ch = payload["challenges"].get(challenge_id) or self._empty_challenge()
            last_chal = float(ch.get("last_submit_unix", 0.0) or 0.0)

        return RateLimitView(
            remaining_global_s=_compute_remaining(now_unix, last_global, global_window_s),
            remaining_per_challenge_s=_compute_remaining(now_unix, last_chal, per_challenge_window_s),
        )

    def try_claim_submit_slot(
        self,
        challenge_id: str,
        global_window_s: float,
        per_challenge_window_s: float,
    ) -> tuple[bool, RateLimitView]:
        """Atomically check both rate-limit windows and, if both are
        clear, claim the slot by stamping ``last_submit_unix`` to now.

        Returns ``(claimed, view)``.  When ``claimed`` is False, the
        view shows how long the caller must still wait.  When True,
        subsequent decide() calls in the same windows will see the
        updated timestamp and be held — preventing concurrent agents
        from both winning AUTO_SUBMIT.

        The actual outcome (correct / wrong) is recorded later via
        ``record_submit``; that call will refresh the timestamp to the
        completion time.  If the caller never calls record_submit (e.g.,
        the platform call is canceled), the claim still stands and the
        rate-limit window will simply expire naturally — conservative
        but safe.
        """
        with _file_lock(self.lock_path):
            payload = self._load()
            now_unix = time.time()
            last_global = float(payload.get("global_last_submit_unix", 0.0) or 0.0)
            ch = payload["challenges"].get(challenge_id) or self._empty_challenge()
            last_chal = float(ch.get("last_submit_unix", 0.0) or 0.0)

            view = RateLimitView(
                remaining_global_s=_compute_remaining(now_unix, last_global, global_window_s),
                remaining_per_challenge_s=_compute_remaining(now_unix, last_chal, per_challenge_window_s),
            )
            if not (view.globally_ok and view.per_challenge_ok):
                return False, view

            now_iso = datetime.now(timezone.utc).isoformat()
            payload["global_last_submit_unix"] = now_unix
            payload["global_last_submit_iso"] = now_iso
            ch_persist = payload["challenges"].setdefault(challenge_id, self._empty_challenge())
            ch_persist["last_submit_unix"] = now_unix
            ch_persist["last_submit_iso"] = now_iso
            self._save(payload)
            return True, view

    def record_submit(
        self,
        challenge_id: str,
        flag: str,
        correct: Optional[bool],
        max_wrong: int,
        force: bool = False,
        platform_response: Optional[str] = None,
    ) -> dict:
        """Persist the submission outcome and freeze the challenge once
        the wrong-count threshold is hit.

        This refreshes ``last_submit_unix`` to the completion time even
        if a prior ``try_claim_submit_slot`` already stamped it; the
        rate-limit window is anchored on the *latest* attempt for the
        challenge.
        """
        with _file_lock(self.lock_path):
            payload = self._load()
            now_unix = time.time()
            now_iso = datetime.now(timezone.utc).isoformat()

            ch = payload["challenges"].setdefault(challenge_id, self._empty_challenge())
            ch["last_submit_unix"] = now_unix
            ch["last_submit_iso"] = now_iso
            payload["global_last_submit_unix"] = now_unix
            payload["global_last_submit_iso"] = now_iso

            if correct is False:
                ch["wrong_count"] = int(ch.get("wrong_count", 0)) + 1

            entry = {
                "ts": now_iso,
                "correct": correct,
                "flag_redacted": _redact_flag(flag),
                "force": bool(force),
            }
            if platform_response:
                entry["platform_response"] = platform_response[:200]
            ch.setdefault("submits", []).append(entry)
            if len(ch["submits"]) > _SUBMIT_HISTORY_LIMIT:
                ch["submits"] = ch["submits"][-_SUBMIT_HISTORY_LIMIT:]

            newly_frozen = False
            if not ch.get("frozen", False) and ch["wrong_count"] >= int(max_wrong):
                ch["frozen"] = True
                ch["frozen_at_iso"] = now_iso
                newly_frozen = True

            self._save(payload)
            return {
                "challenge_id": challenge_id,
                "wrong_count": ch["wrong_count"],
                "frozen": ch["frozen"],
                "newly_frozen": newly_frozen,
                "ts": now_iso,
            }

    def record_outcome_for_pending(
        self,
        challenge_id: str,
        *,
        flag_redacted: str,
        correct: Optional[bool],
        max_wrong: int,
        force: bool = False,
        platform_response: Optional[str] = None,
    ) -> dict:
        """Apply a terminal outcome from the pending-resolve path.

        Same shape as :meth:`record_submit` but takes the already-redacted
        flag string instead of plaintext.  The supervisor uses this so
        ``state/ai_contest_state.json`` never has to persist plaintext
        flags between submit and pending terminalisation.  Codex review
        §2 hygiene fix.
        """
        with _file_lock(self.lock_path):
            payload = self._load()
            now_unix = time.time()
            now_iso = datetime.now(timezone.utc).isoformat()

            ch = payload["challenges"].setdefault(challenge_id, self._empty_challenge())
            ch["last_submit_unix"] = now_unix
            ch["last_submit_iso"] = now_iso
            payload["global_last_submit_unix"] = now_unix
            payload["global_last_submit_iso"] = now_iso

            if correct is False:
                ch["wrong_count"] = int(ch.get("wrong_count", 0)) + 1

            entry: dict[str, Any] = {
                "ts": now_iso,
                "correct": correct,
                "flag_redacted": flag_redacted or "",
                "force": bool(force),
                "via": "pending_resolve",
            }
            if platform_response:
                entry["platform_response"] = platform_response[:200]
            ch.setdefault("submits", []).append(entry)
            if len(ch["submits"]) > _SUBMIT_HISTORY_LIMIT:
                ch["submits"] = ch["submits"][-_SUBMIT_HISTORY_LIMIT:]

            newly_frozen = False
            if not ch.get("frozen", False) and ch["wrong_count"] >= int(max_wrong):
                ch["frozen"] = True
                ch["frozen_at_iso"] = now_iso
                newly_frozen = True

            self._save(payload)
            return {
                "challenge_id": challenge_id,
                "wrong_count": ch["wrong_count"],
                "frozen": ch["frozen"],
                "newly_frozen": newly_frozen,
                "ts": now_iso,
            }

    def force_freeze(self, challenge_id: str, reason: str = "") -> dict:
        with _file_lock(self.lock_path):
            payload = self._load()
            ch = payload["challenges"].setdefault(challenge_id, self._empty_challenge())
            already = ch.get("frozen", False)
            ch["frozen"] = True
            now_iso = datetime.now(timezone.utc).isoformat()
            if not already:
                ch["frozen_at_iso"] = now_iso
            ch["frozen_reason"] = reason or ch.get("frozen_reason", "manual_freeze")
            self._save(payload)
            return {"challenge_id": challenge_id, "already": already, "ts": now_iso}

    def manual_unfreeze(self, challenge_id: str, reason: str) -> dict:
        with _file_lock(self.lock_path):
            payload = self._load()
            ch = payload["challenges"].setdefault(challenge_id, self._empty_challenge())
            ch["frozen"] = False
            ch["unfrozen_at_iso"] = datetime.now(timezone.utc).isoformat()
            ch["unfrozen_reason"] = reason
            self._save(payload)
            return {"challenge_id": challenge_id}

    def snapshot(self) -> dict:
        with _file_lock(self.lock_path):
            return self._load()
