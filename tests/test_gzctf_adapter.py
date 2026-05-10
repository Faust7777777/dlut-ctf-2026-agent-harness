"""GZCTF adapter coverage.

Mocks ``requests.Session.request`` so tests never hit the network.
Exercises every required path from
``docs/opus_ai_identity_handoff.md`` §"P0-1: GZCTF Adapter":
login + cookie reuse, profile/team/game/details, challenge detail,
attachment session reuse, submit returns submitId,
``FlagSubmitted -> Accepted``, ``FlagSubmitted -> WrongAnswer``,
``CheatDetected``, ``NotFound`` not treated as accepted, polling
timeout while still ``FlagSubmitted`` not re-submitting, plaintext /
encrypted mode branching, attachment download, and scope refusal.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from ctf_agents.common.scope import ScopeError
from ctf_agents.submit.gzctf_adapter import (
    GZCTFAdapter,
    GZCTFSubmitOutcome,
    GZCTF_STATUS_MAP,
)


SCOPE_OK = {"allowed_domains": ["gzctf.test"]}


def _resp(json_body=None, *, status_code=200, text=None, headers=None, content=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.headers = headers or {}
    if content is not None:
        r.content = content
        r.text = content.decode("utf-8", errors="ignore")
    elif text is not None:
        r.text = text
    elif json_body is not None:
        r.text = json.dumps(json_body, ensure_ascii=False)
    else:
        r.text = ""
    if json_body is not None:
        r.json.return_value = json_body
    elif text is not None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = {}
    # Stream support for download_attachment
    r.iter_content = MagicMock(
        return_value=iter([content]) if content is not None else iter([b""])
    )
    r.__enter__ = lambda self_: self_
    r.__exit__ = lambda self_, *a: None
    return r


class GZCTFAdapterTest(unittest.TestCase):
    def _adapter(self, **kwargs) -> GZCTFAdapter:
        defaults = dict(
            base_url="https://gzctf.test",
            username="alice",
            password="hunter2",
            scope_cfg=SCOPE_OK,
            default_game_id=42,
        )
        defaults.update(kwargs)
        return GZCTFAdapter(**defaults)

    # ---------------------------------------------------------- scope

    def test_constructor_rejects_url_outside_scope(self):
        with self.assertRaises(ScopeError):
            GZCTFAdapter(
                base_url="https://elsewhere.invalid",
                username="x",
                password="y",
                scope_cfg=SCOPE_OK,
            )

    def test_unconfigured_scope_refused(self):
        with self.assertRaises(ScopeError):
            GZCTFAdapter(
                base_url="https://gzctf.test",
                username="x",
                password="y",
                scope_cfg={},
            )

    # ---------------------------------------------------------- login

    def test_login_sends_credentials_and_persists_cookies(self):
        # Explicitly password mode — auto mode would short-circuit to
        # the cookie path once we seed a cookie below.
        adapter = self._adapter(auth_mode="password")
        cookie_path = Path(tempfile.mktemp(suffix=".json"))
        adapter.cookie_jar_path = str(cookie_path)
        try:
            with patch.object(adapter.session, "request") as req:
                req.return_value = _resp({"ok": True})
                # Pretend the cookie was set by login
                adapter.session.cookies.set("GZCTF_token", "redacted-token")
                result = adapter.login()
                self.assertEqual(result, {"ok": True})
                # Body must include the credentials
                call = req.call_args
                self.assertEqual(call.args[0], "POST")
                self.assertIn("/api/account/login", call.args[1])
                self.assertEqual(
                    call.kwargs["json"], {"userName": "alice", "password": "hunter2"}
                )
            self.assertTrue(cookie_path.exists())
            data = json.loads(cookie_path.read_text())
            self.assertIn("GZCTF_token", data["cookies"])
        finally:
            if cookie_path.exists():
                cookie_path.unlink()

    def test_login_failure_raises(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp(status_code=401, text="Unauthorized")
            with self.assertRaises(RuntimeError) as cm:
                adapter.login()
            self.assertIn("login failed", str(cm.exception))
            # password must NOT appear in the error
            self.assertNotIn("hunter2", str(cm.exception))

    # ---------------------------------------------------------- read

    def test_profile_team_game_details(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp({"id": 1, "userName": "alice"}),
                _resp({"id": 7, "name": "team-a"}),
                _resp({"id": 42, "title": "DLUT"}),
                _resp({
                    "id": 42,
                    "challenges": [
                        {"id": 100, "title": "easy misc", "category": "Misc"},
                    ],
                }),
            ]
            self.assertEqual(adapter.profile()["userName"], "alice")
            self.assertEqual(adapter.current_team()["name"], "team-a")
            self.assertEqual(adapter.game(42)["title"], "DLUT")
            details = adapter.game_details(42)
            self.assertEqual(details["challenges"][0]["id"], 100)

    def test_current_team_normalizes_list_response(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp([{"id": 7, "name": "team-a"}])
            team = adapter.current_team()
            self.assertEqual(team["id"], 7)
            self.assertEqual(team["name"], "team-a")

    def test_challenge_detail_includes_url(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp(
                {"id": 100, "title": "A", "context": {"url": "/files/foo.zip"}}
            )
            ch = adapter.challenge_detail(42, 100)
            self.assertEqual(ch["context"]["url"], "/files/foo.zip")

    # ---------------------------------------------- attachment download

    def test_download_attachment_uses_session_and_writes_file(self):
        adapter = self._adapter()
        body = b"PK\x03\x04binary data here"
        with patch.object(adapter.session, "get") as get:
            get.return_value = _resp(
                content=body,
                headers={"Content-Disposition": 'attachment; filename="puzzle.zip"'},
            )
            with tempfile.TemporaryDirectory() as td:
                target = adapter.download_attachment("/files/puzzle.zip", td)
                self.assertEqual(target.name, "puzzle.zip")
                self.assertEqual(target.read_bytes(), body)
            # Must have been called via the same session (cookies reused)
            self.assertEqual(get.call_count, 1)
            self.assertIn("https://gzctf.test", get.call_args.args[0])

    def test_download_attachment_rejects_out_of_scope(self):
        adapter = self._adapter()
        with self.assertRaises(ScopeError):
            adapter.download_attachment("https://other.invalid/x.zip", "/tmp")

    def test_download_attachment_applies_url_rewrite_before_scope_check(self):
        adapter = self._adapter(
            base_url="http://127.0.0.1:8080",
            scope_cfg={
                "allowed_domains": ["127.0.0.1"],
                "url_rewrites": {"http://files:8081": "http://127.0.0.1:8081"},
            },
        )
        with patch.object(adapter.session, "get") as get:
            get.return_value = _resp(
                content=b"flag{local_static_accept}",
                headers={"Content-Disposition": 'attachment; filename="a.txt"'},
            )
            with tempfile.TemporaryDirectory() as td:
                target = adapter.download_attachment("http://files:8081/a.txt", td)
                self.assertEqual(target.read_bytes(), b"flag{local_static_accept}")
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8081/a.txt")

    # --------------------------------------------------------- submit

    def test_submit_accepted_path_returns_correct_true(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(101),                                  # submit returns submitId
                _resp({"status": "FlagSubmitted"}),          # poll #1
                _resp({"status": "Accepted"}),               # poll #2
            ]
            result = adapter.submit_flag("100", "flag{ok-correct}")
            self.assertTrue(result.correct)
            self.assertTrue(result.ok)
            self.assertIn("Accepted", result.message)
            # full flag must NOT be in the message
            self.assertNotIn("ok-correct", result.message)

    def test_submit_wrong_path_returns_correct_false(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(102),
                _resp({"status": "FlagSubmitted"}),
                _resp({"status": "WrongAnswer"}),
            ]
            result = adapter.submit_flag("100", "flag{nope}")
            self.assertFalse(result.correct, "WrongAnswer must map correct=False")
            # ok=True is the right shape: transport succeeded; correct=False
            # carries the judgment.  This mirrors CTFdAdapter.
            self.assertTrue(result.ok)
            self.assertIn("WrongAnswer", result.message)

    def test_cheat_detected_maps_to_failure_with_kind(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(103),
                _resp({"status": "CheatDetected"}),
            ]
            outcome = adapter.submit_flag_for_game(42, 100, "flag{looks-shared}")
            self.assertEqual(outcome.kind, "cheat")
            self.assertFalse(outcome.correct)
            self.assertTrue(outcome.terminal)

    def test_not_found_is_not_accepted(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(104),
                _resp({"status": "NotFound"}),
            ]
            outcome = adapter.submit_flag_for_game(42, 100, "flag{wrong-id}")
            self.assertIsNone(outcome.correct)
            self.assertEqual(outcome.kind, "not_found")
            self.assertTrue(outcome.terminal)

    def test_pending_timeout_does_not_resubmit(self):
        # Drive through submit_flag_for_game so the submit POST is in
        # the call chain.  Then verify exactly ONE POST happened even
        # though we polled multiple times and never reached terminal.
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(105),                                  # submit POST
                _resp({"status": "FlagSubmitted"}),          # poll 1
                _resp({"status": "FlagSubmitted"}),          # poll 2
            ]
            with patch("ctf_agents.submit.gzctf_adapter.time.sleep"):
                outcome = adapter.submit_flag_for_game(
                    42, 100, "flag{pending}",
                    poll_timeout_s=0.0, poll_interval_s=0.0,
                )
        post_calls = [c for c in req.mock_calls if c.args and c.args[0] == "POST"]
        self.assertEqual(len(post_calls), 1, "must not resubmit while pending")
        self.assertEqual(outcome.status, "FlagSubmitted")
        self.assertFalse(outcome.terminal)
        self.assertEqual(outcome.kind, "pending")

    def test_submit_id_extracted_from_object_envelope(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp({"submitId": 999}),
                _resp({"status": "Accepted"}),
            ]
            outcome = adapter.submit_flag_for_game(42, 100, "flag{ok}")
            self.assertEqual(outcome.submit_id, 999)
            self.assertTrue(outcome.correct)

    def test_submit_payload_mode_plaintext_default_auto(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(201),
                _resp({"status": "Accepted"}),
            ]
            adapter.submit_flag("100", "flag{plain}")
            # First call is the submit POST; check body is plain
            submit_call = req.call_args_list[0]
            self.assertEqual(submit_call.kwargs["json"], {"flag": "flag{plain}"})

    def test_submit_payload_mode_encrypted_raises_with_marker(self):
        adapter = self._adapter(submit_payload_mode="encrypted")
        with self.assertRaises(NotImplementedError) as cm:
            adapter.submit_flag("100", "flag{x}")
        self.assertIn("NEEDS_REAL_INSTANCE_VALIDATION", str(cm.exception))

    def test_submit_without_active_game_raises(self):
        adapter = self._adapter(default_game_id=None)
        with self.assertRaises(RuntimeError) as cm:
            adapter.submit_flag("100", "flag{x}")
        self.assertIn("active game", str(cm.exception))

    def test_set_active_game_makes_submit_use_it(self):
        adapter = self._adapter(default_game_id=None)
        adapter.set_active_game(77)
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(301),
                _resp({"status": "Accepted"}),
            ]
            adapter.submit_flag("9", "flag{x}")
        # Ensure path includes /api/game/77/...
        submit_call = req.call_args_list[0]
        self.assertIn("/api/game/77/challenges/9", submit_call.args[1])

    # -------------------------------------------- poll independently

    def test_poll_submission_status_resolves_pending_to_accepted(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp({"status": "FlagSubmitted"}),
                _resp({"status": "Accepted"}),
            ]
            with patch("ctf_agents.submit.gzctf_adapter.time.sleep"):
                outcome = adapter.poll_submission_status(
                    42, 100, 555, timeout_s=10, interval_s=0
                )
            self.assertEqual(outcome.status, "Accepted")
            self.assertTrue(outcome.correct)

    # ---------------------------------------- containers (smoke)

    def test_create_and_extend_container(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp({"instanceEntry": "10.0.0.1:8080"}),
                _resp({"extendedAt": "..."}),
            ]
            self.assertEqual(
                adapter.create_container(42, 100)["instanceEntry"],
                "10.0.0.1:8080",
            )
            self.assertIn("extendedAt", adapter.extend_container(42, 100))

    def test_delete_container_tolerates_404(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp(status_code=404, text="not running")
            res = adapter.delete_container(42, 100)
            self.assertEqual(res["status_code"], 404)

    # ----------------------------------- status mapping coverage

    def test_status_map_covers_all_canonical_states(self):
        for s in ("Accepted", "WrongAnswer", "CheatDetected", "NotFound", "FlagSubmitted"):
            self.assertIn(s, GZCTF_STATUS_MAP)

    def test_status_extracted_from_data_envelope(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(401),
                _resp({"data": {"status": "Accepted"}}),
            ]
            outcome = adapter.submit_flag_for_game(42, 100, "flag{ok}")
            self.assertEqual(outcome.status, "Accepted")
            self.assertTrue(outcome.correct)

    # -- Bare AnswerResult string per official GZCTF OpenAPI ------

    def test_status_bare_string_accepted(self):
        # Real GZCTF GET /status/{submitId} returns the AnswerResult
        # enum as a bare JSON string ("Accepted") — not an envelope.
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(text='"FlagSubmitted"'),  # submit returns bare str via JSON
                _resp(text='"Accepted"'),       # status returns bare str
            ]
            # Submit returns a string, not an int — _extract_submit_id
            # reads the integer-style envelope but in real GZCTF the
            # submit response is a Submission object.  Switch to
            # explicit submitId via {"submitId": N} for this test.
        # Re-use a richer mock pattern: submit returns submitId int, then
        # status endpoint returns the BARE string per OpenAPI.
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(101),                       # submit → submitId 101
                _resp(text='"FlagSubmitted"'),    # status #1: bare string pending
                _resp(text='"Accepted"'),         # status #2: bare string accepted
            ]
            with patch("ctf_agents.submit.gzctf_adapter.time.sleep"):
                outcome = adapter.submit_flag_for_game(
                    42, 100, "flag{ok}",
                    poll_timeout_s=5.0, poll_interval_s=0.0,
                )
        self.assertEqual(outcome.status, "Accepted")
        self.assertTrue(outcome.correct)
        self.assertEqual(outcome.kind, "accepted")

    def test_status_bare_string_wrong_answer(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(102),
                _resp(text='"WrongAnswer"'),
            ]
            outcome = adapter.submit_flag_for_game(42, 100, "flag{nope}")
        self.assertEqual(outcome.status, "WrongAnswer")
        self.assertFalse(outcome.correct)
        self.assertEqual(outcome.kind, "wrong")

    def test_status_bare_string_cheat_detected(self):
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(103),
                _resp(text='"CheatDetected"'),
            ]
            outcome = adapter.submit_flag_for_game(42, 100, "flag{shared}")
        self.assertEqual(outcome.kind, "cheat")
        self.assertFalse(outcome.correct)
        self.assertTrue(outcome.terminal)

    # -- cookie jar formats (legacy dict / JSON array / Netscape) -

    def _adapter_with_jar(self, jar_path: Path) -> GZCTFAdapter:
        return GZCTFAdapter(
            base_url="https://gzctf.dlut.edu.cn",
            scope_cfg={"allowed_domains": ["gzctf.dlut.edu.cn", "dlut.edu.cn"]},
            cookie_jar_path=str(jar_path),
            auth_mode="cookie",
        )

    def test_loader_legacy_dict_format(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.json"
            p.write_text(json.dumps({"cookies": {"a": "1", "b": "2"}}), encoding="utf-8")
            adapter = self._adapter_with_jar(p)
            names = {c.name for c in adapter.session.cookies}
            self.assertEqual(names, {"a", "b"})
            self.assertEqual(adapter._loaded_cookie_format, "legacy_dict")

    def test_loader_json_array_preserves_domain_path(self):
        # Cookie-Editor / Chrome export shape, with two cookies that
        # have the SAME name under different domains.  The richer
        # loader must preserve both rather than have the second
        # silently overwrite the first.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.json"
            p.write_text(
                json.dumps([
                    {
                        "name": "JSESSIONID",
                        "value": "sso-session-token",
                        "domain": ".dlut.edu.cn",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                        "expirationDate": 4_102_444_800,
                    },
                    {
                        "name": "JSESSIONID",
                        "value": "gzctf-session-token",
                        "domain": "gzctf.dlut.edu.cn",
                        "path": "/api",
                        "secure": True,
                    },
                    {
                        "name": "GZCTF_token",
                        "value": "bearer-from-export",
                        "domain": "gzctf.dlut.edu.cn",
                        "path": "/",
                    },
                ]),
                encoding="utf-8",
            )
            adapter = self._adapter_with_jar(p)
            self.assertEqual(adapter._loaded_cookie_format, "json_array")

            # Both JSESSIONIDs must exist, distinguishable by domain
            domain_value_pairs = {(c.name, c.domain, c.value) for c in adapter.session.cookies}
            self.assertIn(("JSESSIONID", ".dlut.edu.cn", "sso-session-token"), domain_value_pairs)
            self.assertIn(("JSESSIONID", "gzctf.dlut.edu.cn", "gzctf-session-token"), domain_value_pairs)
            self.assertIn(("GZCTF_token", "gzctf.dlut.edu.cn", "bearer-from-export"), domain_value_pairs)

    def test_loader_netscape_format_preserves_domain_path(self):
        netscape = (
            "# Netscape HTTP Cookie File\n"
            "# Generated by curl --cookie-jar\n"
            ".dlut.edu.cn\tTRUE\t/\tTRUE\t4102444800\tJSESSIONID\tsso-session-token\n"
            "gzctf.dlut.edu.cn\tFALSE\t/api\tTRUE\t0\tJSESSIONID\tgzctf-session-token\n"
            "#HttpOnly_gzctf.dlut.edu.cn\tFALSE\t/\tTRUE\t0\tGZCTF_token\tbearer-from-curl\n"
            "\n"
            "# trailing blank line + comment must be tolerated\n"
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.txt"
            p.write_text(netscape, encoding="utf-8")
            adapter = self._adapter_with_jar(p)
            self.assertEqual(adapter._loaded_cookie_format, "netscape")

            triples = {(c.name, c.domain, c.value) for c in adapter.session.cookies}
            self.assertIn(("JSESSIONID", ".dlut.edu.cn", "sso-session-token"), triples)
            self.assertIn(("JSESSIONID", "gzctf.dlut.edu.cn", "gzctf-session-token"), triples)
            self.assertIn(("GZCTF_token", "gzctf.dlut.edu.cn", "bearer-from-curl"), triples)

    def test_loader_same_name_different_domain_no_overwrite(self):
        # Tightest assertion of the SSO scenario: same name + same
        # path under different hosts, both stay alive.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.json"
            p.write_text(
                json.dumps([
                    {"name": "JSESSIONID", "value": "v1", "domain": "a.dlut.edu.cn", "path": "/"},
                    {"name": "JSESSIONID", "value": "v2", "domain": "b.dlut.edu.cn", "path": "/"},
                    {"name": "JSESSIONID", "value": "v3", "domain": "a.dlut.edu.cn", "path": "/api"},
                ]),
                encoding="utf-8",
            )
            adapter = self._adapter_with_jar(p)
            jar = adapter.session.cookies
            cookies_at_a_root = list(jar.list_paths())
            # Three cookies should coexist (same name, different domain/path)
            triples = {(c.name, c.domain, c.path, c.value) for c in jar}
            self.assertEqual(len(triples), 3)
            self.assertIn(("JSESSIONID", "a.dlut.edu.cn", "/", "v1"), triples)
            self.assertIn(("JSESSIONID", "b.dlut.edu.cn", "/", "v2"), triples)
            self.assertIn(("JSESSIONID", "a.dlut.edu.cn", "/api", "v3"), triples)

    def test_loader_handles_malformed_entries_gracefully(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.json"
            p.write_text(
                json.dumps([
                    {"name": "good", "value": "1", "domain": "x.test"},
                    {"name": "", "value": "2", "domain": "x.test"},   # empty name skipped
                    {"value": "no-name", "domain": "x.test"},          # no name skipped
                    "string-not-object",                                # non-dict skipped
                    {"name": "expires-bad", "value": "4", "domain": "x.test", "expirationDate": "not-a-number"},
                ]),
                encoding="utf-8",
            )
            adapter = self._adapter_with_jar(p)
            names = {c.name for c in adapter.session.cookies}
            self.assertEqual(names, {"good", "expires-bad"})

    def test_loader_unparseable_jar_is_warning_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.json"
            p.write_text("{not valid json at all", encoding="utf-8")
            # Must not raise during construction
            adapter = self._adapter_with_jar(p)
            self.assertEqual(len(adapter.session.cookies), 0)

    def test_loader_empty_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cookies.json"
            p.write_text("   \n  \n", encoding="utf-8")
            adapter = self._adapter_with_jar(p)
            self.assertEqual(len(adapter.session.cookies), 0)

    # -- auth_mode (cookie / password / auto) ---------------------

    def test_auth_mode_cookie_requires_cookie_jar(self):
        with self.assertRaises(ValueError) as cm:
            GZCTFAdapter(
                base_url="https://gzctf.test",
                scope_cfg=SCOPE_OK,
                auth_mode="cookie",
            )
        self.assertIn("cookie_jar_path", str(cm.exception))

    def test_auth_mode_password_requires_credentials(self):
        with self.assertRaises(ValueError) as cm:
            GZCTFAdapter(
                base_url="https://gzctf.test",
                scope_cfg=SCOPE_OK,
                auth_mode="password",
            )
        self.assertIn("username", str(cm.exception))

    def test_auth_mode_unknown_raises(self):
        with self.assertRaises(ValueError):
            GZCTFAdapter(
                base_url="https://gzctf.test",
                scope_cfg=SCOPE_OK,
                auth_mode="oauth-banana",
            )

    def test_login_cookie_mode_verifies_via_profile_no_password_post(self):
        # Pre-seeded cookies (as if loaded from a jar) + cookie mode →
        # login() never sends a password; just hits /api/account/profile.
        adapter = GZCTFAdapter(
            base_url="https://gzctf.test",
            scope_cfg=SCOPE_OK,
            cookie_jar_path="/tmp/some/path",  # path doesn't need to exist for this test
            auth_mode="cookie",
        )
        # Simulate cookies that were loaded from the jar
        adapter.session.cookies.set("GZCTF_token", "abcdef-fake-cookie")
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp({"id": 1, "userName": "alice"})
            result = adapter.login()
            self.assertEqual(result["userName"], "alice")
            # The single call must be GET /api/account/profile
            self.assertEqual(req.call_count, 1)
            method, url = req.call_args.args[0], req.call_args.args[1]
            self.assertEqual(method, "GET")
            self.assertIn("/api/account/profile", url)

    def test_login_cookie_mode_no_cookies_raises(self):
        adapter = GZCTFAdapter(
            base_url="https://gzctf.test",
            scope_cfg=SCOPE_OK,
            cookie_jar_path="/tmp/missing",
            auth_mode="cookie",
        )
        with self.assertRaises(RuntimeError) as cm:
            adapter.login()
        self.assertIn("no cookies loaded", str(cm.exception))

    def test_login_cookie_mode_expired_cookie_raises(self):
        adapter = GZCTFAdapter(
            base_url="https://gzctf.test",
            scope_cfg=SCOPE_OK,
            cookie_jar_path="/tmp/whatever",
            auth_mode="cookie",
        )
        adapter.session.cookies.set("GZCTF_token", "stale")
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp(status_code=401, text="Unauthorized")
            with self.assertRaises(RuntimeError) as cm:
                adapter.login()
            self.assertIn("not authenticated", str(cm.exception))

    def test_login_auto_mode_prefers_cookies_when_present(self):
        adapter = GZCTFAdapter(
            base_url="https://gzctf.test",
            username="alice", password="hunter2",
            scope_cfg=SCOPE_OK,
            auth_mode="auto",
        )
        adapter.session.cookies.set("GZCTF_token", "from-jar")
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp({"userName": "alice"})
            adapter.login()
            # Single call to profile (cookie verify), no password POST
            self.assertEqual(req.call_count, 1)
            self.assertEqual(req.call_args.args[0], "GET")
            self.assertIn("/api/account/profile", req.call_args.args[1])

    def test_login_auto_mode_falls_back_to_password_when_no_cookies(self):
        adapter = GZCTFAdapter(
            base_url="https://gzctf.test",
            username="alice", password="hunter2",
            scope_cfg=SCOPE_OK,
            auth_mode="auto",
        )
        # No cookies seeded
        with patch.object(adapter.session, "request") as req:
            req.return_value = _resp({"ok": True})
            adapter.login()
            # Single call, POST /api/account/login
            self.assertEqual(req.call_args.args[0], "POST")
            self.assertIn("/api/account/login", req.call_args.args[1])

    def test_login_auto_mode_falls_back_when_cookie_verify_fails(self):
        adapter = GZCTFAdapter(
            base_url="https://gzctf.test",
            username="alice", password="hunter2",
            scope_cfg=SCOPE_OK,
            auth_mode="auto",
        )
        adapter.session.cookies.set("GZCTF_token", "expired")
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(status_code=401, text="Unauthorized"),  # cookie verify fails
                _resp({"ok": True}),                          # password login OK
            ]
            adapter.login()
            self.assertEqual(req.call_count, 2)
            # Second call is the password POST
            self.assertEqual(req.call_args_list[1].args[0], "POST")
            self.assertIn("/api/account/login", req.call_args_list[1].args[1])

    def test_status_bare_unknown_string_falls_back_to_pending(self):
        # Defensive: a bare string we don't recognise must NOT be
        # treated as terminal — it falls through to pending so the
        # supervisor's poll loop keeps trying.
        adapter = self._adapter()
        with patch.object(adapter.session, "request") as req:
            req.side_effect = [
                _resp(104),
                _resp(text='"UnknownEnumValue"'),
            ]
            with patch("ctf_agents.submit.gzctf_adapter.time.sleep"):
                outcome = adapter.submit_flag_for_game(
                    42, 100, "flag{x}",
                    poll_timeout_s=0.0, poll_interval_s=0.0,
                )
        # Status defaults to fallback "FlagSubmitted" (pending), not terminal
        self.assertFalse(outcome.terminal)
        self.assertEqual(outcome.status, "FlagSubmitted")


if __name__ == "__main__":
    unittest.main()
