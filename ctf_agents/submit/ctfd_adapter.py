from __future__ import annotations
import requests
from .platform_adapter import SubmitResult
from ctf_agents.common.scope import assert_url_in_scope
class CTFdAdapter:
    """CTFd 常见 API 适配骨架。赛前必须用平台测试入口验证字段。"""
    def __init__(self, base_url: str, token: str, scope_cfg: dict):
        self.base_url = base_url.rstrip("/"); self.token = token; self.scope_cfg = scope_cfg; assert_url_in_scope(self.base_url, scope_cfg)
    def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        url = f"{self.base_url}/api/v1/challenges/attempt"; assert_url_in_scope(url, self.scope_cfg)
        r = requests.post(url, json={"challenge_id": challenge_id, "submission": flag}, headers={"Authorization": f"Token {self.token}", "Content-Type": "application/json"}, timeout=8)
        try: data = r.json()
        except Exception: data = {"status_code": r.status_code, "text": r.text[:200]}
        text = str(data).lower(); correct = True if "correct" in text or "already solved" in text else False if "incorrect" in text or "wrong" in text else None
        return SubmitResult(ok=r.ok, correct=correct, message=str(data)[:500], raw=data)
