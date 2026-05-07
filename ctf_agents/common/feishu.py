from __future__ import annotations

import base64, hashlib, hmac, time
from typing import Any
import requests


def _sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_text(webhook: str, text: str, secret: str = "", timeout: float = 5.0) -> dict[str, Any]:
    if not webhook:
        return {"ok": False, "error": "empty webhook"}
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _sign(timestamp, secret)
    resp = requests.post(webhook, json=payload, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        data = {"status_code": resp.status_code, "text": resp.text[:200]}
    return {"ok": resp.ok, "response": data}
