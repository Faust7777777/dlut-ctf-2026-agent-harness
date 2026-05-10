"""GZCTF platform adapter.

Minimum contest path implementation per
``docs/opus_ai_identity_handoff.md`` §"P0-1: GZCTF Adapter".  Wraps
GZCTF's REST API behind the same ``PlatformAdapter`` protocol the rest
of the toolkit consumes, so ``FlagGuard`` / ``SkillWorkflow`` /
``ai_contest_supervisor`` can call ``submit_flag()`` without caring
which platform is on the other side.

Boundaries (must not be widened by callers):
  - this module is the only HTTP egress to the contest platform
  - it never decides whether a flag is safe — that's ``FlagGuard``
  - it never persists secrets in ordinary logs (cookies, password,
    full flags are all redacted)

Submit payload mode:
  ``plaintext``  : POST ``{"flag": "..."}`` directly.
  ``encrypted``  : stub — real GZCTF deployments may require
                   ``encryptApiData(payload, apiPublicKey)`` per the
                   frontend.  Implementation here raises with a
                   ``NEEDS_REAL_INSTANCE_VALIDATION`` marker; the 5/9
                   rehearsal will pin the real mode.
  ``auto``       : try plaintext, log a warning if we see the
                   "encryption required" signature so the operator can
                   flip the mode for the contest run.

The adapter holds a ``requests.Session`` to reuse cookies across
``login`` → ``submit`` → ``status`` cycles.  Cookies can be persisted
to ``cookie_jar_path`` so a supervisor restart keeps the auth.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from requests.cookies import create_cookie

from ctf_agents.common.scope import assert_url_in_scope
from .platform_adapter import SubmitResult


logger = logging.getLogger("gzctf_adapter")

# Status enum from GZCTF AnswerResult.  We map to (correct, terminal):
#   - terminal=True  -> stop polling
#   - correct=True   -> Accepted
#   - correct=False  -> WrongAnswer / CheatDetected
#   - correct=None   -> indeterminate (NotFound / FlagSubmitted)
GZCTF_STATUS_MAP: dict[str, dict[str, Any]] = {
    "Accepted":      {"correct": True,  "terminal": True,  "kind": "accepted"},
    "WrongAnswer":   {"correct": False, "terminal": True,  "kind": "wrong"},
    "CheatDetected": {"correct": False, "terminal": True,  "kind": "cheat"},
    "NotFound":      {"correct": None,  "terminal": True,  "kind": "not_found"},
    "FlagSubmitted": {"correct": None,  "terminal": False, "kind": "pending"},
}

# Frontend tokens that hint encrypted payload is required (best-effort
# sniff against unknown deployments; refined on 5/9 with real data).
ENCRYPTED_HINT_TOKENS = (
    "encryptapidata",
    "需要加密",
    "encryption required",
    "invalid public key",
)

DEFAULT_TIMEOUT_S = 10.0


def _redact_flag(flag: str) -> str:
    if not flag:
        return ""
    if len(flag) <= 14:
        return flag[:6] + "…"
    return flag[:6] + "…" + flag[-4:]


def _flag_hash(flag: str) -> str:
    return hashlib.sha256((flag or "").strip().encode("utf-8")).hexdigest()[:16]


@dataclass
class GZCTFSubmitOutcome:
    """Richer result than ``SubmitResult`` so the supervisor can drive
    state machine decisions (cheat → global disable, etc.) without
    re-parsing the platform response."""
    submit_id: Optional[int]
    status: str
    correct: Optional[bool]
    terminal: bool
    kind: str
    raw: dict[str, Any]


class GZCTFAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cookie_jar_path: Optional[str] = None,
        token: Optional[str] = None,
        scope_cfg: Optional[dict] = None,
        submit_payload_mode: str = "auto",
        api_public_key: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        default_game_id: Optional[int] = None,
        auth_mode: str = "auto",
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._password = password
        self.cookie_jar_path = cookie_jar_path
        self.token = token
        self.scope_cfg = scope_cfg or {}
        self.url_rewrites = self.scope_cfg.get("url_rewrites") or {}
        if submit_payload_mode not in {"auto", "plaintext", "encrypted"}:
            raise ValueError(f"unknown submit_payload_mode: {submit_payload_mode}")
        self.submit_payload_mode = submit_payload_mode
        self.api_public_key = api_public_key
        self.timeout_s = timeout_s
        self.default_game_id = default_game_id
        self._active_game_id: Optional[int] = default_game_id

        if auth_mode not in {"auto", "password", "cookie"}:
            raise ValueError(f"unknown auth_mode: {auth_mode}")
        if auth_mode == "password" and not (username and password):
            raise ValueError("auth_mode='password' requires username + password")
        if auth_mode == "cookie" and not cookie_jar_path:
            raise ValueError("auth_mode='cookie' requires cookie_jar_path")
        self.auth_mode = auth_mode

        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Accept"] = "application/json"
        self.session.headers["Content-Type"] = "application/json"

        # Scope-check the base URL up front so misconfiguration fails
        # before any cookies/credentials are sent.
        assert_url_in_scope(self.base_url, self.scope_cfg)

        self._load_cookies()

    # -------------------------------------------------------------- I/O

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self._url(path)
        assert_url_in_scope(url, self.scope_cfg)
        kwargs.setdefault("timeout", self.timeout_s)
        return self.session.request(method, url, **kwargs)

    def _request_json(self, method: str, path: str, **kwargs) -> dict:
        resp = self._request(method, path, **kwargs)
        if resp.status_code >= 400:
            text = (resp.text or "")[:200]
            raise RuntimeError(f"{method} {path}: HTTP {resp.status_code}: {text}")
        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text[:1000]}

    def _load_cookies(self) -> None:
        """Load cookies from ``cookie_jar_path``.

        Three formats are accepted (auto-detected by content):

        1. Netscape / Mozilla ``cookies.txt`` — the format ``curl -b``
           and most browser extensions emit.  Tab-separated:
           ``domain\\tflag\\tpath\\tsecure\\texpires\\tname\\tvalue``.
           Required for multi-domain SSO sessions where the same
           cookie name (e.g. ``JSESSIONID``) appears under different
           hosts.

        2. JSON array exported by Chrome / Cookie-Editor:
           ``[{name,value,domain,path,secure,httpOnly,
              expirationDate|expires}]``.

        3. Legacy ``{"cookies": {"name": "value"}}`` — single-host
           fallback, no domain isolation.  Kept for backward compat
           with our own ``_save_cookies`` output.

        Sets ``self._loaded_cookie_format`` so a future
        ``_save_cookies`` could round-trip in the same shape.
        """
        if not self.cookie_jar_path:
            return
        p = Path(self.cookie_jar_path)
        if not p.exists():
            return
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("failed to read cookie jar %s: %s", p, exc)
            return

        stripped = text.lstrip()
        if not stripped:
            return

        try:
            if stripped[0] in ("[", "{"):
                data = json.loads(text)
                if isinstance(data, list):
                    n = self._ingest_cookie_list(data)
                    self._loaded_cookie_format = "json_array"
                elif isinstance(data, dict):
                    n = self._ingest_cookie_legacy_dict(data)
                    self._loaded_cookie_format = "legacy_dict"
                else:
                    raise RuntimeError(
                        f"cookie jar JSON top-level must be array or object, got {type(data).__name__}"
                    )
            else:
                n = self._ingest_cookie_netscape(text)
                self._loaded_cookie_format = "netscape"
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            logger.warning("failed to parse cookie jar %s: %s", p, exc)
            return

        logger.info("cookies loaded from %s (%d entries, format=%s)",
                    p, n, getattr(self, "_loaded_cookie_format", "?"))

    def _ingest_cookie_legacy_dict(self, data: dict) -> int:
        """Legacy ``{"cookies": {"name": "value"}}`` shape.

        No domain/path/secure metadata.  Cookies set this way live in
        the jar without a domain anchor; ``requests`` attaches them
        only when the request URL matches an explicit domain or no
        cookie has overruled them.  Use one of the richer formats for
        multi-domain SSO sessions.
        """
        n = 0
        cookies_dict = data.get("cookies")
        if not isinstance(cookies_dict, dict):
            return 0
        for name, value in cookies_dict.items():
            if not name or value is None:
                continue
            self.session.cookies.set(str(name), str(value))
            n += 1
        return n

    def _ingest_cookie_list(self, data: list) -> int:
        """Chrome / Cookie-Editor JSON array.

        Each entry must have ``name`` + ``value``; all other fields
        are best-effort.  ``expirationDate`` (Chrome) / ``expires``
        (Cookie-Editor) are normalised to int seconds.  Same-name
        cookies under different ``domain``/``path`` are kept separate
        — this is the whole point of supporting this format.
        """
        n = 0
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if not name or value is None:
                continue
            expires_raw = entry.get("expires")
            if expires_raw is None:
                expires_raw = entry.get("expirationDate")
            expires: Optional[int] = None
            if isinstance(expires_raw, (int, float)):
                expires = int(expires_raw)
            elif isinstance(expires_raw, str) and expires_raw.strip():
                try:
                    expires = int(float(expires_raw))
                except ValueError:
                    expires = None
            cookie = create_cookie(
                name=str(name),
                value=str(value),
                domain=str(entry.get("domain") or ""),
                path=str(entry.get("path") or "/"),
                secure=bool(entry.get("secure", False)),
                expires=expires,
                rest={"HttpOnly": str(bool(entry.get("httpOnly", False)))},
            )
            self.session.cookies.set_cookie(cookie)
            n += 1
        return n

    def _ingest_cookie_netscape(self, text: str) -> int:
        """Netscape / Mozilla ``cookies.txt``.

        Tab-separated, ``#``-prefixed comments.  Lines with the
        ``#HttpOnly_`` prefix on the domain field are valid (curl /
        wget convention) and we strip the prefix so the cookie is
        loaded into the jar with the bare domain.
        """
        n = 0
        for line in text.splitlines():
            line = line.rstrip("\r")
            if not line.strip():
                continue
            # Comments allowed; "#HttpOnly_" is a curl marker, not a comment.
            if line.startswith("#") and not line.startswith("#HttpOnly_"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, path, secure, expires_s, name, value = parts[:7]
            if domain.startswith("#HttpOnly_"):
                domain = domain[len("#HttpOnly_"):]
            if not name:
                continue
            try:
                expires = int(expires_s) if expires_s and expires_s != "0" else None
            except ValueError:
                expires = None
            cookie = create_cookie(
                name=name,
                value=value,
                domain=domain,
                path=path or "/",
                secure=(secure.lower() == "true"),
                expires=expires,
            )
            self.session.cookies.set_cookie(cookie)
            n += 1
        return n

    def _save_cookies(self) -> None:
        if not self.cookie_jar_path:
            return
        p = Path(self.cookie_jar_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        cookies = {c.name: c.value for c in self.session.cookies}
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, p)

    # --------------------------------------------------------- account

    def login(self) -> dict:
        """Establish or verify an authenticated session.

        Behaviour depends on ``auth_mode``:

          - ``password``: always POST credentials.
          - ``cookie``  : verify existing cookies by hitting
                          ``/api/account/profile``; never sends a
                          password.  Supports the campus SSO use case
                          where the operator exported cookies from a
                          Windows browser session into a jar that this
                          adapter loads.
          - ``auto``    : try cookies first when present, fall back to
                          password.

        See ``runbooks/campus_sso_cookie_reuse.md`` for the cookie
        export procedure.
        """
        if self.auth_mode == "cookie":
            return self._verify_cookie_session()
        if self.auth_mode == "password":
            return self._login_with_password()
        # auto: prefer cookies if loaded, otherwise password
        if len(self.session.cookies) > 0:
            try:
                return self._verify_cookie_session()
            except RuntimeError as exc:
                logger.info("cookie verify failed (%s); falling back to password", exc)
        return self._login_with_password()

    def _verify_cookie_session(self) -> dict:
        if len(self.session.cookies) == 0:
            raise RuntimeError(
                "auth_mode='cookie' but no cookies loaded — check "
                "cookie_jar_path and runbook for the export procedure"
            )
        resp = self._request("GET", "/api/account/profile")
        if resp.status_code >= 400:
            raise RuntimeError(
                f"cookie session not authenticated: HTTP {resp.status_code} "
                f"(cookies expired or wrong domain)"
            )
        try:
            payload = resp.json() if resp.text else {}
        except ValueError:
            payload = {"raw": resp.text[:200]}
        # Never log cookie values.
        logger.info(
            "cookie session verified user=%s",
            payload.get("userName", "?") if isinstance(payload, dict) else "?",
        )
        return payload

    def _login_with_password(self) -> dict:
        if not (self.username and self._password):
            raise RuntimeError(
                "auth_mode requires username + password but neither was provided"
            )
        body = {"userName": self.username, "password": self._password}
        resp = self._request("POST", "/api/account/login", json=body)
        if resp.status_code >= 400:
            text = (resp.text or "")[:200]
            raise RuntimeError(f"login failed: HTTP {resp.status_code}: {text}")
        try:
            payload = resp.json() if resp.text else {}
        except ValueError:
            payload = {"raw": resp.text[:200]}
        self._save_cookies()
        # Never log the password / cookie value.
        logger.info("login ok user=%s", self.username)
        return payload

    def profile(self) -> dict:
        return self._request_json("GET", "/api/account/profile")

    def current_team(self) -> dict:
        payload = self._request_json("GET", "/api/team")
        # Official OpenAPI describes GET /api/team as TeamInfoModel[],
        # while earlier adapter assumptions used a single object.  For
        # the supervisor's "current team" path, normalize the common
        # single-team case to a dict.
        if isinstance(payload, list):
            return payload[0] if payload else {}
        return payload

    # ----------------------------------------------------------- games

    def list_games(self, count: int = 50, skip: int = 0) -> dict:
        return self._request_json(
            "GET",
            "/api/game",
            params={"count": count, "skip": skip},
        )

    def game(self, game_id: int) -> dict:
        return self._request_json("GET", f"/api/game/{int(game_id)}")

    def game_details(self, game_id: int) -> dict:
        return self._request_json("GET", f"/api/game/{int(game_id)}/details")

    def join_game(self, game_id: int, body: Optional[dict] = None) -> dict:
        return self._request_json("POST", f"/api/game/{int(game_id)}", json=body or {})

    # ---------------------------------------------------- challenge

    def challenge_detail(self, game_id: int, challenge_id: int) -> dict:
        return self._request_json(
            "GET",
            f"/api/game/{int(game_id)}/challenges/{int(challenge_id)}",
        )

    # -------------------------------------------------- attachment

    def download_attachment(self, url: str, output_dir: str | Path) -> Path:
        """Download a single attachment using the adapter's session so
        cookies persist.  ``url`` may be relative to ``base_url`` or
        absolute (still scope-checked)."""
        full_url = url if urlparse(url).netloc else self._url(url)
        full_url = self._rewrite_url(full_url)
        assert_url_in_scope(full_url, self.scope_cfg)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        with self.session.get(full_url, stream=True, timeout=self.timeout_s * 6) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"attachment download failed: HTTP {resp.status_code} {full_url}"
                )
            # Content-Disposition fallback to URL basename; never trust it
            # blindly — strip path traversal segments.
            name = self._derive_attachment_name(resp, full_url)
            target = out_dir / name
            with target.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 15):
                    if chunk:
                        f.write(chunk)
        logger.info("attachment saved: %s", target)
        return target

    def _rewrite_url(self, url: str) -> str:
        """Apply explicit local URL rewrites.

        This is mainly for local Docker rehearsal: GZCTF stores a
        Remote attachment URL reachable from inside the compose network
        (``http://files:8081/...``), while the host-side adapter must
        download through the host-published loopback port
        (``http://127.0.0.1:8081/...``).
        """
        for src, dst in self.url_rewrites.items():
            if src and url.startswith(src):
                return dst + url[len(src):]
        return url

    @staticmethod
    def _derive_attachment_name(resp: requests.Response, full_url: str) -> str:
        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            raw = cd.split("filename=", 1)[1].strip().strip('"')
            raw = raw.split(";", 1)[0]
            if raw:
                return Path(raw).name or "attachment.bin"
        path = urlparse(full_url).path
        return Path(path).name or "attachment.bin"

    # -------------------------------------------------- container

    def create_container(self, game_id: int, challenge_id: int) -> dict:
        return self._request_json(
            "POST",
            f"/api/game/{int(game_id)}/container/{int(challenge_id)}",
        )

    def delete_container(self, game_id: int, challenge_id: int) -> dict:
        resp = self._request(
            "DELETE",
            f"/api/game/{int(game_id)}/container/{int(challenge_id)}",
        )
        if resp.status_code >= 400 and resp.status_code != 404:
            raise RuntimeError(
                f"delete_container failed: HTTP {resp.status_code}: {(resp.text or '')[:200]}"
            )
        return {"status_code": resp.status_code}

    def extend_container(self, game_id: int, challenge_id: int) -> dict:
        return self._request_json(
            "POST",
            f"/api/game/{int(game_id)}/container/{int(challenge_id)}/extend",
        )

    # ------------------------------------------------------ submit

    def set_active_game(self, game_id: int) -> None:
        self._active_game_id = int(game_id)

    def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        """``PlatformAdapter`` protocol entry point — uses the active
        game id (set via constructor or ``set_active_game``)."""
        if self._active_game_id is None:
            raise RuntimeError(
                "submit_flag called without an active game; "
                "set default_game_id at construction or call set_active_game()"
            )
        outcome = self.submit_flag_for_game(self._active_game_id, int(challenge_id), flag)
        # Map richer outcome into the canonical SubmitResult shape used
        # downstream by FlagGuard.record_outcome().
        message = (
            f"gzctf {outcome.kind} status={outcome.status} "
            f"submit_id={outcome.submit_id} flag={_redact_flag(flag)}"
        )
        return SubmitResult(
            ok=outcome.terminal or outcome.correct is True,
            correct=outcome.correct,
            message=message,
            raw=outcome.raw,
        )

    def submit_flag_for_game(
        self,
        game_id: int,
        challenge_id: int,
        flag: str,
        *,
        poll_timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> GZCTFSubmitOutcome:
        body = self._build_submit_payload(flag)
        path = f"/api/game/{int(game_id)}/challenges/{int(challenge_id)}"
        resp = self._request("POST", path, json=body)
        if resp.status_code >= 400:
            text = (resp.text or "")[:300]
            self._sniff_encryption_required(text)
            raise RuntimeError(
                f"submit failed: HTTP {resp.status_code} flag={_redact_flag(flag)} body_excerpt={text}"
            )
        try:
            payload = resp.json() if resp.text else {}
        except ValueError:
            payload = {"raw": resp.text[:500]}

        # GZCTF returns an integer submitId.  Older clients return a
        # JSON-encoded number; some return {"submitId": N}; tolerate both.
        submit_id = self._extract_submit_id(payload)
        outcome = self._poll_until_terminal(
            game_id=game_id,
            challenge_id=challenge_id,
            submit_id=submit_id,
            initial_status="FlagSubmitted",
            timeout_s=poll_timeout_s,
            interval_s=poll_interval_s,
        )
        outcome.raw.setdefault("submit_response", payload)
        logger.info(
            "submit chal=%s game=%s id=%s status=%s flag=%s",
            challenge_id, game_id, submit_id, outcome.status, _redact_flag(flag),
        )
        return outcome

    def _build_submit_payload(self, flag: str) -> dict:
        if self.submit_payload_mode == "plaintext":
            return {"flag": flag}
        if self.submit_payload_mode == "auto":
            return {"flag": flag}
        if self.submit_payload_mode == "encrypted":
            # NEEDS_REAL_INSTANCE_VALIDATION: real impl would encrypt
            # via api_public_key per GZCTF frontend's encryptApiData;
            # 5/9 rehearsal pins this.
            raise NotImplementedError(
                "encrypted submit_payload_mode not implemented "
                "(NEEDS_REAL_INSTANCE_VALIDATION on 5/9)"
            )
        raise RuntimeError(f"unreachable: mode={self.submit_payload_mode}")

    def _sniff_encryption_required(self, body_excerpt: str) -> None:
        if not body_excerpt:
            return
        lower = body_excerpt.lower()
        for token in ENCRYPTED_HINT_TOKENS:
            if token in lower:
                logger.warning(
                    "submit response hint suggests encrypted payload required; "
                    "set submit_payload_mode='encrypted' on 5/9 once validated"
                )
                return

    @staticmethod
    def _extract_submit_id(payload: Any) -> Optional[int]:
        if isinstance(payload, int):
            return payload
        if isinstance(payload, dict):
            for k in ("submitId", "submit_id", "id"):
                if k in payload and isinstance(payload[k], int):
                    return payload[k]
        if isinstance(payload, str):
            try:
                return int(payload.strip())
            except ValueError:
                return None
        return None

    def poll_submission_status(
        self,
        game_id: int,
        challenge_id: int,
        submit_id: int,
        timeout_s: float = 60.0,
        interval_s: float = 2.0,
    ) -> GZCTFSubmitOutcome:
        return self._poll_until_terminal(
            game_id=game_id,
            challenge_id=challenge_id,
            submit_id=submit_id,
            initial_status="FlagSubmitted",
            timeout_s=timeout_s,
            interval_s=interval_s,
        )

    def _poll_until_terminal(
        self,
        *,
        game_id: int,
        challenge_id: int,
        submit_id: Optional[int],
        initial_status: str = "FlagSubmitted",
        timeout_s: float = 60.0,
        interval_s: float = 2.0,
    ) -> GZCTFSubmitOutcome:
        if submit_id is None:
            return GZCTFSubmitOutcome(
                submit_id=None,
                status=initial_status,
                correct=None,
                terminal=False,
                kind="pending_no_id",
                raw={},
            )
        path = f"/api/game/{int(game_id)}/challenges/{int(challenge_id)}/status/{int(submit_id)}"
        deadline = time.monotonic() + max(0.0, timeout_s)
        last_payload: dict = {}
        last_status = initial_status
        while True:
            try:
                payload = self._request_json("GET", path)
            except RuntimeError as exc:
                logger.warning("status poll error: %s", exc)
                payload = {"error": str(exc)[:200]}
            last_payload = payload if isinstance(payload, dict) else {"raw": payload}
            last_status = self._status_from_payload(last_payload, last_status)
            mapped = GZCTF_STATUS_MAP.get(last_status, {
                "correct": None, "terminal": False, "kind": "unknown",
            })
            if mapped["terminal"] or time.monotonic() >= deadline:
                return GZCTFSubmitOutcome(
                    submit_id=submit_id,
                    status=last_status,
                    correct=mapped["correct"],
                    terminal=mapped["terminal"],
                    kind=mapped["kind"],
                    raw=last_payload,
                )
            time.sleep(max(0.05, interval_s))

    @staticmethod
    def _status_from_payload(payload: Any, fallback: str) -> str:
        # GZCTF OpenAPI: GET /status/{submitId} returns the AnswerResult
        # enum as a bare JSON string (e.g. "Accepted").  Older
        # deployments / forks may wrap it in {"status": "..."} or
        # {"data": {"status": "..."}}; we support both shapes plus the
        # {"raw": "Accepted"} envelope our _request_json fallback emits
        # when the body was not strict JSON.
        known = {"Accepted", "WrongAnswer", "CheatDetected", "NotFound", "FlagSubmitted"}

        if isinstance(payload, str):
            stripped = payload.strip().strip('"')
            return stripped if stripped in known else fallback

        if isinstance(payload, dict):
            raw = payload.get("raw")
            if isinstance(raw, str):
                stripped = raw.strip().strip('"')
                if stripped in known:
                    return stripped
            for key in ("status", "answerResult", "result"):
                if key in payload and isinstance(payload[key], str):
                    return payload[key]
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("status", "answerResult", "result"):
                    if key in data and isinstance(data[key], str):
                        return data[key]
            if isinstance(data, str):
                stripped = data.strip().strip('"')
                if stripped in known:
                    return stripped
        return fallback


__all__ = [
    "GZCTFAdapter",
    "GZCTFSubmitOutcome",
    "GZCTF_STATUS_MAP",
]
