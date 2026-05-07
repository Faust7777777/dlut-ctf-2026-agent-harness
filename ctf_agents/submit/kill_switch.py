"""Panic-button helper.

The kill switch is a sentinel file (``.auto_submit_off`` by default).  Its
existence is checked at the very top of every ``flag_guard.decide()``;
when present, every AUTO_SUBMIT decision is downgraded to
HUMAN_REVIEW.  Kept as a file (not a state.json field) so it takes
effect *immediately* even if the state-store is locked or mid-write.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


DEFAULT_KILL_SWITCH_FILE = ".auto_submit_off"


def kill_switch_path(project_root: str | Path, kill_switch_file: str = DEFAULT_KILL_SWITCH_FILE) -> Path:
    return Path(project_root) / kill_switch_file


def is_active(path: str | Path) -> bool:
    return Path(path).exists()


def activate(path: str | Path, reason: str = "") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"activated_at={datetime.now(timezone.utc).isoformat()}\n"
        f"reason={reason}\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


def deactivate(path: str | Path) -> bool:
    p = Path(path)
    if p.exists():
        p.unlink()
        return True
    return False
