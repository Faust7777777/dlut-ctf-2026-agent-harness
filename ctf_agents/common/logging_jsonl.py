from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json, os, re, uuid

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|authorization|cookie)\s*[:=]\s*[^\s]+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(session|ctf[_-]?token)=[A-Za-z0-9._%\-]+"),
]


def redact_text(s: str) -> str:
    out = s
    for pat in SECRET_PATTERNS:
        out = pat.sub(lambda m: m.group(0).split(":")[0].split("=")[0] + "=<REDACTED>", out)
    return out.replace(str(Path.home()), "~")


class JsonlLogger:
    def __init__(self, logs_dir: str | Path = "logs", run_id: str | None = None):
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or os.getenv("RUN_ID") or datetime.now().strftime("run-%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.path = Path(logs_dir) / f"{self.run_id}.jsonl"

    def event(self, event_type: str, actor: str, message: str, challenge_id: str | None = None, category: str | None = None, data: dict[str, Any] | None = None, confidence: float | None = None, cost_usd_est: float | None = None, duration_ms: int | None = None, redact: bool = True) -> dict[str, Any]:
        msg = redact_text(message) if redact else message
        redacted_data = data or {}
        if redact:
            redacted_data = json.loads(redact_text(json.dumps(redacted_data, ensure_ascii=False)))
        obj = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, "event_type": event_type, "actor": actor, "challenge_id": challenge_id, "category": category, "message": msg, "data": redacted_data, "redacted": redact}
        if confidence is not None: obj["confidence"] = round(float(confidence), 4)
        if cost_usd_est is not None: obj["cost_usd_est"] = round(float(cost_usd_est), 4)
        if duration_ms is not None: obj["duration_ms"] = int(duration_ms)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return obj
