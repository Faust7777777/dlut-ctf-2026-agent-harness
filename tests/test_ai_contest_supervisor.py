"""AI contest supervisor coverage.

Validates the deterministic state machine in
``scripts/ai_contest_supervisor.py`` against a mock GZCTF adapter.
Focus: correct state transitions, dedup, freeze, accepted-stop,
restart safety, cheat-detected global disable, no-resubmit-on-pending.

Adapter calls are mocked at the object level — supervisor never sees
real HTTP.  Agents are also injected directly so each test can drive
the candidate it wants.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard  # noqa: E402
from ctf_agents.submit.gzctf_adapter import GZCTFSubmitOutcome  # noqa: E402
from ctf_agents.submit.decisions import Decision, GuardDecision, HoldReason  # noqa: E402

# Import via spec-style path so it works without scripts/ being a package
import importlib.util  # noqa: E402

_SUP_PATH = PROJECT / "scripts" / "ai_contest_supervisor.py"
_spec = importlib.util.spec_from_file_location("ai_contest_supervisor", _SUP_PATH)
sup_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sup_mod)  # type: ignore[attr-defined]
AIContestSupervisor = sup_mod.AIContestSupervisor


def _high_conf_candidate(challenge_id: str, flag: str = "flag{ok-strong}") -> FlagCandidate:
    return FlagCandidate(
        challenge_id=str(challenge_id),
        flag=flag,
        category="misc",
        evidence_count=4,
        extraction_confidence=1.0,
        agent_votes=[flag] * 3,
    )


def _make_outcome(kind: str, *, status: str | None = None) -> GZCTFSubmitOutcome:
    map_correct = {"accepted": True, "wrong": False, "cheat": False, "not_found": None, "pending": None}
    map_status = {"accepted": "Accepted", "wrong": "WrongAnswer", "cheat": "CheatDetected",
                  "not_found": "NotFound", "pending": "FlagSubmitted"}
    return GZCTFSubmitOutcome(
        submit_id=42,
        status=status or map_status[kind],
        correct=map_correct[kind],
        terminal=kind != "pending",
        kind=kind,
        raw={},
    )


class _MockAdapter:
    def __init__(self, *, challenges: list[dict], details_by_id: dict | None = None,
                 attachment_paths: dict | None = None, submit_outcomes: dict | None = None,
                 challenges_shape: str = "list"):
        self._challenges = challenges
        self._details_by_id = details_by_id or {}
        self._attachment_paths = attachment_paths or {}
        # submit_outcomes: challenge_id -> [outcome_kind, outcome_kind, ...]
        # consumed in order; if exhausted, raises so tests notice
        self._submit_outcomes = {k: list(v) for k, v in (submit_outcomes or {}).items()}
        self.submit_calls: list[tuple] = []
        self.poll_calls: list[tuple] = []
        self.attachment_calls: list[tuple] = []
        self._active_game = None
        # "list" → flat list (legacy); "dict" → dict-by-category (real GZCTF)
        self._challenges_shape = challenges_shape

    def set_active_game(self, gid):
        self._active_game = gid

    def login(self): return {"ok": True}
    def profile(self): return {"userName": "tester"}
    def current_team(self): return {"id": 1}
    def game(self, gid): return {"id": gid}
    def game_details(self, gid):
        if self._challenges_shape == "dict":
            by_cat: dict[str, list[dict]] = {}
            for ch in self._challenges:
                by_cat.setdefault(ch.get("category", "Misc"), []).append(dict(ch))
            return {"id": gid, "challenges": by_cat}
        return {"id": gid, "challenges": list(self._challenges)}

    def challenge_detail(self, gid, cid):
        return self._details_by_id.get(str(cid), {"id": cid, "context": {}})

    def download_attachment(self, url, output_dir):
        cid_match = next(
            (c for c, p in self._attachment_paths.items() if p["url"] == url),
            None,
        )
        if cid_match is None:
            raise RuntimeError(f"no attachment fixture for {url}")
        target = Path(output_dir) / self._attachment_paths[cid_match]["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture")
        self.attachment_calls.append((cid_match, str(target)))
        return target

    def submit_flag_for_game(self, *, game_id, challenge_id, flag, poll_timeout_s=60.0, poll_interval_s=2.0):
        self.submit_calls.append((game_id, challenge_id, flag))
        outcomes = self._submit_outcomes.get(str(challenge_id), [])
        if not outcomes:
            raise RuntimeError(f"no submit outcome left for challenge {challenge_id}")
        kind = outcomes.pop(0)
        return _make_outcome(kind)

    def poll_submission_status(self, game_id, challenge_id, submit_id, *,
                               timeout_s=0.0, interval_s=0.0):
        self.poll_calls.append((game_id, challenge_id, submit_id))
        outcomes = self._submit_outcomes.get(str(challenge_id), [])
        if not outcomes:
            # Default behaviour: still pending until terminal queued
            return _make_outcome("pending")
        kind = outcomes.pop(0)
        return _make_outcome(kind)


def _make_cfg(tmp: Path, *, max_wrong: int = 1) -> dict:
    return {
        "gzctf": {
            "base_url": "https://gzctf.test",
            "game_id": 99,
            "poll_timeout_s": 1.0,
            "poll_interval_s": 0.0,
        },
        "submit": {
            "auto_submit": True,
            "auto_submit_categories": ["misc", "forensics", "crypto"],
            "min_conf_auto_submit": 0.92,
            "min_conf_human_review": 0.70,
            "max_wrong_per_challenge": max_wrong,
            "min_seconds_between_submits_global": 0,
            "min_seconds_between_submits_per_challenge": 0,
            "flag_regex": r"(?i)flag\{[^{}\s]{4,200}\}",
            "state_path": str(tmp / "submission_state.json"),
            "kill_switch_file": ".auto_submit_off",
            "force_submit_min_reason_length": 10,
            "pwn_reverse_force_human_review": True,
        },
        "agent": {
            "enabled_categories": ["misc", "forensics"],
            "challenge_loop_interval_s": 0,
            "challenge_solve_timeout_s": 60,
            "global_run_timeout_s": 5,
            "heartbeat_interval_s": 0,
        },
        "paths": {
            "state_dir": str(tmp / "state"),
            "artifacts_dir": str(tmp / "artifacts"),
            "logs_dir": str(tmp / "logs"),
            "locks_dir": str(tmp / "state" / "locks"),
        },
        "feishu": {"enabled": False},
    }


def _make_supervisor(*, cfg, adapter, agents):
    guard = FlagGuard(project_root=Path(cfg["paths"]["state_dir"]).parent, submit_cfg=cfg["submit"])
    return AIContestSupervisor(cfg=cfg, adapter=adapter, guard=guard, agents=agents)


class AIContestSupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._patch_project = patch.object(sup_mod, "PROJECT", self.root)
        self._patch_project.start()

    def tearDown(self):
        self._patch_project.stop()
        self.tmp.cleanup()

    def _write_runtime_capabilities(self, payload: dict | None) -> Path:
        path = self.root / "state" / "runtime_capabilities.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            if path.exists():
                path.unlink()
            return path
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _base_runtime_capabilities(self, *, crypto_lattice=True, crypto_classic=True, pwn=True, web_static=True) -> dict:
        return {
            "capabilities": {
                "crypto_lattice": {"available": crypto_lattice},
                "crypto_classic": {"available": crypto_classic},
                "pwn": {"available": pwn},
                "web_static": {"available": web_static},
            }
        }

    # ---- 1. first sync creates state ----------------------------

    def test_sync_registers_new_challenges(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[
            {"id": 11, "title": "Misc 1", "category": "Misc"},
            {"id": 12, "title": "Pwn 1", "category": "Pwn"},
        ])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        self.assertIn("11", sup.state["challenges"])
        self.assertIn("12", sup.state["challenges"])
        self.assertEqual(sup.state["challenges"]["11"]["category"], "misc")
        route_control = sup.state["challenges"]["11"].get("route_control")
        self.assertIsInstance(route_control, dict)
        self.assertEqual(route_control["current_family"], "misc.initial")
        self.assertEqual(route_control["route_decision"], "continue_route")
        self.assertEqual(route_control["public_search_status"], "not_required")

    # ---- 2. attachment downloaded -------------------------------

    def test_attachment_challenge_downloads(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            details_by_id={"11": {"id": 11, "context": {"url": "/files/puzzle.zip"}}},
            attachment_paths={"11": {"url": "/files/puzzle.zip", "name": "puzzle.zip"}},
            submit_outcomes={"11": ["accepted"]},
        )
        # Agent that returns a candidate using the attachment
        def agent(challenge):
            return _high_conf_candidate(challenge.id) if challenge.attachments else None

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(len(adapter.attachment_calls), 1)
        self.assertEqual(sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)

    # ---- 3. no candidate -> no submit ---------------------------

    def test_no_candidate_no_submit(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(adapter.submit_calls, [])
        self.assertEqual(sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_NO_AGENT)
        route = sup.state["challenges"]["11"]["route_control"]
        self.assertEqual(route["route_decision"], "spawn_public_search")
        self.assertIn("route_not_exhausted", route["no_candidate_blockers"])
        self.assertIn("public_search_required", route["no_candidate_blockers"])

    # ---- 4. accepted path --------------------------------------

    def test_candidate_guard_allow_submit_accepted(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )
        sup = _make_supervisor(
            cfg=cfg, adapter=adapter,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id)},
        )
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(len(adapter.submit_calls), 1)
        cstate = sup.state["challenges"]["11"]
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)
        self.assertIsNotNone(cstate["accepted_flag_hash"])
        self.assertNotIn("pending_record_payload", cstate)

    # ---- 5. wrong -> frozen, no second submit ------------------

    def test_wrong_freezes_and_no_resubmit(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["wrong"]},
        )
        sup = _make_supervisor(
            cfg=cfg, adapter=adapter,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id)},
        )
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        # now run another tick — must not resubmit (frozen state)
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(len(adapter.submit_calls), 1)
        cstate = sup.state["challenges"]["11"]
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_WRONG_FROZEN)
        self.assertNotIn("pending_record_payload", cstate)

    # ---- 6. duplicate flag → only one submit -------------------

    def test_duplicate_flag_blocked_locally(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending", "pending"]},
        )

        # Agent returns same candidate every time
        flag = "flag{same-every-time-static}"
        def agent(ch):
            return _high_conf_candidate(ch.id, flag=flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(len(adapter.submit_calls), 1, "duplicate flag must not resubmit")

    def test_duplicate_candidate_does_not_reset_route_stall_counter(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending"]},
        )

        flag = "flag{same-every-time-static}"
        def agent(ch):
            return _high_conf_candidate(ch.id, flag=flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        cstate = sup.state["challenges"]["11"]
        cstate["state"] = sup_mod.CHALLENGE_STATE_DETAIL_FETCHED
        cstate["route_control"]["same_family_no_delta_count"] = 1

        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(cstate["route_control"]["same_family_no_delta_count"], 2)

    # ---- 7. pending → does not resubmit ------------------------

    def test_pending_does_not_resubmit(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending"]},
        )

        def agent(ch):
            return _high_conf_candidate(ch.id, flag="flag{still-pending}")

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        # second tick: even if agent produces same candidate again,
        # dedup blocks resubmit.
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_PENDING)

    # ---- 8. cheat detected → global disable --------------------

    def test_cheat_detected_disables_global_submit(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[
                {"id": 11, "title": "M", "category": "Misc"},
                {"id": 12, "title": "F", "category": "Forensics"},
            ],
            submit_outcomes={"11": ["cheat"], "12": ["accepted"]},
        )

        def agent(ch):
            return _high_conf_candidate(ch.id, flag=f"flag{{ch-{ch.id}}}")

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent, "forensics": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})
        self.assertTrue(sup._global_submit_disabled)
        # second challenge must not get submitted
        sup.step_challenge({"id": 12, "category": "Forensics"})
        self.assertEqual(len(adapter.submit_calls), 1, "global disable must block subsequent submits")

    def test_cheat_detected_disable_survives_restart(self):
        cfg = _make_cfg(self.root)
        adapter1 = _MockAdapter(
            challenges=[
                {"id": 11, "title": "M", "category": "Misc"},
                {"id": 12, "title": "F", "category": "Forensics"},
            ],
            submit_outcomes={"11": ["cheat"]},
        )

        def agent(ch):
            return _high_conf_candidate(ch.id, flag=f"flag{{restart-{ch.id}}}")

        sup1 = _make_supervisor(cfg=cfg, adapter=adapter1, agents={"misc": agent, "forensics": agent})
        sup1.sync_challenges()
        sup1.step_challenge({"id": 11, "category": "Misc"})
        self.assertTrue(sup1.state["global_submit_disabled"])

        adapter2 = _MockAdapter(
            challenges=[
                {"id": 11, "title": "M", "category": "Misc"},
                {"id": 12, "title": "F", "category": "Forensics"},
            ],
            submit_outcomes={"12": ["accepted"]},
        )
        sup2 = _make_supervisor(cfg=cfg, adapter=adapter2, agents={"misc": agent, "forensics": agent})
        self.assertTrue(sup2._global_submit_disabled)
        sup2.sync_challenges()
        sup2.step_challenge({"id": 12, "category": "Forensics"})

        self.assertEqual(adapter2.submit_calls, [])

    # ---- 9. restart from state -> no repeated submits ----------

    def test_restart_skips_already_terminal_challenges(self):
        cfg = _make_cfg(self.root)
        adapter1 = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )
        sup1 = _make_supervisor(
            cfg=cfg, adapter=adapter1,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id)},
        )
        sup1.sync_challenges()
        sup1.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(sup1.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)

        # New supervisor instance with the same state file
        adapter2 = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={},  # any submit attempt would error
        )
        sup2 = _make_supervisor(
            cfg=cfg, adapter=adapter2,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id)},
        )
        sup2.sync_challenges()
        sup2.step_challenge({"id": 11, "category": "Misc"})
        self.assertEqual(adapter2.submit_calls, [])
        self.assertEqual(sup2.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)

    def test_route_control_state_round_trips_through_restart(self):
        cfg = _make_cfg(self.root)
        adapter1 = _MockAdapter(
            challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}],
        )
        sup1 = _make_supervisor(cfg=cfg, adapter=adapter1, agents={})
        sup1.sync_challenges()
        route = sup1.state["challenges"]["11"]["route_control"]
        route["current_family"] = "crypto.lattice.multivariate_coppersmith"
        route["failure_type"] = "helper_bound_limit"
        route["route_decision"] = "spawn_public_search"
        route["public_search_status"] = "required"
        route["no_candidate_blockers"] = ["public_search_required", "helper_bound_limit"]
        sup1._save_state()

        adapter2 = _MockAdapter(
            challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}],
        )
        sup2 = _make_supervisor(cfg=cfg, adapter=adapter2, agents={})

        route2 = sup2.state["challenges"]["11"]["route_control"]
        self.assertEqual(
            route2["current_family"],
            "crypto.lattice.multivariate_coppersmith",
        )
        self.assertEqual(route2["failure_type"], "helper_bound_limit")
        self.assertEqual(route2["public_search_status"], "required")
        self.assertIn("helper_bound_limit", route2["no_candidate_blockers"])

    def test_no_candidate_is_blocked_until_route_exhaustion(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        cstate = sup.state["challenges"]["11"]
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_NO_AGENT)
        route = cstate["route_control"]
        self.assertEqual(route["route_decision"], "spawn_public_search")
        self.assertIn("public_search_required", route["no_candidate_blockers"])
        self.assertIn("expert_review_required", route["no_candidate_blockers"])
        self.assertIn("candidate_queue_not_empty", route["no_candidate_blockers"])
        request_path = self.root / "artifacts" / "challenges" / "11" / "public_search_request.json"
        self.assertTrue(request_path.exists())
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(request["action"], "spawn_public_search")
        self.assertEqual(request["current_family"], "misc.initial")

        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn("route_control_decision", log_text)
        self.assertIn("route_control_action", log_text)
        self.assertIn("spawn_public_search", log_text)

    def test_public_search_request_enters_durable_route_state(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()

        sup.step_challenge({"id": 11, "category": "Misc"})

        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        action_state = cstate["route_control_action_state"]["public_search"]
        self.assertEqual(route["public_search_status"], "running")
        self.assertNotIn("spawn_public_search", route["pending_actions"])
        self.assertEqual(action_state["status"], "running")
        self.assertTrue(action_state["request_path"].endswith("public_search_request.json"))
        sup._save_state()

        adapter2 = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup2 = _make_supervisor(cfg=cfg, adapter=adapter2, agents={"misc": lambda ch: None})
        restored = sup2.state["challenges"]["11"]
        self.assertEqual(restored["route_control"]["public_search_status"], "running")
        self.assertEqual(
            restored["route_control_action_state"]["public_search"]["status"],
            "running",
        )

    def test_public_search_running_blocks_ordinary_agent_dispatch(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        calls = []

        def agent(challenge):
            calls.append(challenge.id)
            return _high_conf_candidate(challenge.id)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        route = sup.state["challenges"]["11"]["route_control"]
        route.update(
            {
                "public_search_status": "running",
                "route_decision": "spawn_public_search",
                "no_candidate_blockers": ["public_search_required"],
            }
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(calls, [])
        self.assertEqual(adapter.submit_calls, [])
        cstate = sup.state["challenges"]["11"]
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_DETAIL_FETCHED)
        self.assertEqual(cstate["route_control"]["public_search_status"], "running")

    def test_public_search_result_must_have_complete_dispositions_to_unblock(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        calls = []

        def agent(challenge):
            calls.append(challenge.id)
            return _high_conf_candidate(challenge.id)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "public_search_status": "running",
                "route_decision": "spawn_public_search",
                "no_candidate_blockers": ["public_search_required"],
            }
        )
        result_path = self.root / "artifacts" / "challenges" / "11" / "public_search_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "coverage": ["partial"],
                    "dispositions": ["incomplete"],
                    "results": [
                        {
                            "query": "M",
                            "url": "https://example.test/writeup",
                            "summary": "missing disposition",
                            "disposition": "incomplete",
                        }
                    ],
                    "conclusion": "one hit needs disposition",
                    "no_candidate_blockers": ["public search coverage incomplete"],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(calls, [])
        self.assertEqual(cstate["route_control"]["public_search_status"], "running")

        result_path.unlink()
        ledger_path = self.root / "artifacts" / "challenges" / "11" / "public_search_ledger.json"
        ledger_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "coverage": ["complete"],
                    "dispositions": ["reject_with_reason"],
                    "results": [
                        {
                            "query": "M",
                            "url": "https://example.test/writeup",
                            "summary": "writeup for a different challenge",
                            "disposition": "reject_with_reason",
                            "notes": "different challenge",
                        }
                    ],
                    "conclusion": "no reusable public solve",
                    "no_candidate_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(calls, ["11"])
        self.assertEqual(cstate["route_control"]["public_search_status"], "complete")
        action_state = cstate["route_control_action_state"]["public_search"]
        self.assertEqual(action_state["status"], "complete")
        self.assertTrue(action_state["result_path"].endswith("public_search_ledger.json"))

    def test_expert_review_decision_writes_review_packet(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        route = sup.state["challenges"]["11"]["route_control"]
        route.update(
            {
                "public_search_status": "complete",
                "no_candidate_blockers": [],
            }
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        packet_path = self.root / "artifacts" / "challenges" / "11" / "expert_review_packet.json"
        self.assertTrue(packet_path.exists())
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["action"], "spawn_expert_review")
        self.assertEqual(packet["current_family"], "misc.initial")
        self.assertIn("expert_review_required", packet["no_candidate_blockers"])
        route = sup.state["challenges"]["11"]["route_control"]
        action_state = sup.state["challenges"]["11"]["route_control_action_state"]["expert_review"]
        self.assertEqual(route["expert_review_status"], "running")
        self.assertEqual(action_state["status"], "running")
        self.assertTrue(action_state["request_path"].endswith("expert_review_packet.json"))

    def test_expert_review_result_updates_next_family_and_blocks_dispatch_once(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        cfg["agent"]["enabled_categories"] = ["crypto"]
        calls = []

        def agent(challenge):
            calls.append(challenge.id)
            return None

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": agent})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "current_family": "crypto.lattice.small_roots",
                "public_search_status": "complete",
                "expert_review_status": "running",
                "route_decision": "spawn_expert_review",
                "no_candidate_blockers": ["expert_review_required"],
            }
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})
        self.assertEqual(calls, [])

        result_path = self.root / "artifacts" / "challenges" / "11" / "expert_review_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "verdict": "switch_family",
                    "failure_class": "structural_failure",
                    "continue_current_family": False,
                    "next_families": ["crypto.algebraic.elimination"],
                    "first_experiment": {
                        "description": "derive elimination equation",
                        "lane": "main",
                    },
                    "stop_condition": "new family exhausted",
                    "no_candidate_blockers": ["elimination experiment not run"],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        route = cstate["route_control"]
        self.assertEqual(route["expert_review_status"], "complete")
        self.assertEqual(route["current_family"], "crypto.algebraic.elimination")
        self.assertNotIn("switch_family", route["pending_actions"])
        self.assertTrue(route["family_switch_done"])
        self.assertEqual(
            cstate["route_control_action_state"]["family_switch"]["from_family"],
            "crypto.lattice.small_roots",
        )
        self.assertEqual(
            cstate["route_control_action_state"]["family_switch"]["to_family"],
            "crypto.algebraic.elimination",
        )
        self.assertIn("elimination experiment not run", route["no_candidate_blockers"])
        self.assertEqual(calls, ["11"])

    def test_public_search_blocked_by_rules_marks_status_and_unblocks_dispatch(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        calls = []

        def agent(challenge):
            calls.append(challenge.id)
            return None

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "public_search_status": "running",
                "route_decision": "spawn_public_search",
                "no_candidate_blockers": ["public_search_required"],
            }
        )

        result_path = self.root / "artifacts" / "challenges" / "11" / "public_search_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "status": "blocked_by_rules",
                    "coverage": ["rule-blocked: external network disallowed"],
                    "dispositions": ["substitute_offline"],
                    "results": [
                        {
                            "query": "M",
                            "source": "offline://approved/notebook",
                            "summary": "approved offline reference per contest network policy",
                            "disposition": "substitute_offline",
                        }
                    ],
                    "conclusion": "rule-blocked from external search; offline substitute approved",
                    "no_candidate_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        self.assertEqual(route["public_search_status"], "blocked_by_rules")
        self.assertNotIn("public_search_required", route["no_candidate_blockers"])
        action_state = cstate["route_control_action_state"]["public_search"]
        self.assertEqual(action_state["status"], "blocked_by_rules")
        self.assertEqual(calls, ["11"])

    def test_public_search_blocked_by_rules_invalid_payload_does_not_unblock(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        calls = []

        def agent(challenge):
            calls.append(challenge.id)
            return None

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "public_search_status": "running",
                "route_decision": "spawn_public_search",
                "no_candidate_blockers": ["public_search_required"],
            }
        )

        result_path = self.root / "artifacts" / "challenges" / "11" / "public_search_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "status": "blocked_by_rules",
                    "coverage": ["rule-blocked"],
                    "results": [],
                    "no_candidate_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        self.assertEqual(route["public_search_status"], "running")
        self.assertIn("public_search_required", route["no_candidate_blockers"])
        action_state = cstate["route_control_action_state"]["public_search"]
        self.assertEqual(action_state.get("last_error"), "invalid_or_incomplete_result")
        self.assertEqual(calls, [])

    def test_expert_review_switch_family_attributes_first_experiment_to_new_family(self):
        cfg = _make_cfg(self.root)
        cfg["agent"]["enabled_categories"] = ["crypto"]
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "current_family": "crypto.lattice.small_roots",
                "public_search_status": "complete",
                "expert_review_status": "running",
                "route_decision": "spawn_expert_review",
                "no_candidate_blockers": ["expert_review_required"],
            }
        )

        first_experiment = {
            "description": "derive elimination ideal of degree 3",
            "lane": "main",
        }
        result_path = self.root / "artifacts" / "challenges" / "11" / "expert_review_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "verdict": "switch_family",
                    "failure_class": "structural_failure",
                    "continue_current_family": False,
                    "next_families": ["crypto.algebraic.elimination"],
                    "first_experiment": first_experiment,
                    "stop_condition": "elimination ideal exhausted",
                    "no_candidate_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        route = cstate["route_control"]
        self.assertEqual(route["current_family"], "crypto.algebraic.elimination")

        tried = route["tried_families"]
        by_family = {entry["family"]: entry for entry in tried}
        self.assertIn("crypto.lattice.small_roots", by_family)
        old_entry = by_family["crypto.lattice.small_roots"]
        self.assertEqual(old_entry["status"], "cut")
        self.assertNotIn(first_experiment, old_entry["experiments"])

        self.assertIn("crypto.algebraic.elimination", by_family)
        new_entry = by_family["crypto.algebraic.elimination"]
        self.assertIn(first_experiment, new_entry["experiments"])

        family_switch = cstate["route_control_action_state"]["family_switch"]
        self.assertEqual(family_switch["status"], "complete")
        self.assertEqual(family_switch["from_family"], "crypto.lattice.small_roots")
        self.assertEqual(family_switch["to_family"], "crypto.algebraic.elimination")
        self.assertNotIn("pending_first_experiment", family_switch)

    def test_expert_cut_route_clears_stale_next_family_and_pending_switch(self):
        cfg = _make_cfg(self.root)
        cfg["agent"]["enabled_categories"] = ["crypto"]
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "current_family": "crypto.lattice.small_roots",
                # Stale suggestion left over from an earlier
                # public-search ledger / persistent-lane update.
                "next_family": "crypto.algebraic.elimination",
                "public_search_status": "complete",
                "expert_review_status": "running",
                "route_decision": "spawn_expert_review",
                "no_candidate_blockers": ["expert_review_required"],
                # Stale switch_family queued by an earlier evaluate_route tick.
                "pending_actions": ["switch_family"],
            }
        )

        result_path = self.root / "artifacts" / "challenges" / "11" / "expert_review_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "verdict": "cut_route",
                    "failure_class": "wrong_target",
                    "continue_current_family": False,
                    "next_families": [],
                    "first_experiment": {
                        "description": "must be dropped: expert says cut",
                        "lane": "main",
                    },
                    "stop_condition": "no viable family",
                    "no_candidate_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        self.assertEqual(route["current_family"], "crypto.lattice.small_roots")
        self.assertIsNone(route["next_family"])
        self.assertNotIn("switch_family", route["pending_actions"])
        self.assertNotIn("cut_route", route["pending_actions"])

        tried = route["tried_families"]
        families = {entry["family"] for entry in tried}
        self.assertIn("crypto.lattice.small_roots", families)
        self.assertNotIn("crypto.algebraic.elimination", families)
        old_entry = next(item for item in tried if item["family"] == "crypto.lattice.small_roots")
        self.assertEqual(old_entry["status"], "cut")
        # The dropped first_experiment must not have been attached
        # anywhere on the ledger.
        for entry in tried:
            for experiment in entry["experiments"]:
                self.assertNotIn(
                    "must be dropped: expert says cut",
                    experiment.get("description", ""),
                )

        family_switch_state = cstate["route_control_action_state"]["family_switch"]
        self.assertNotIn("pending_first_experiment", family_switch_state)
        # No real switch happened, action_state.family_switch.status is
        # still its default ("not_started"); never flipped to "complete".
        self.assertNotEqual(family_switch_state.get("status"), "complete")

    def test_switch_family_preserves_existing_cut_reason(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "current_family": "crypto.lattice.small_roots",
                "next_family": "crypto.algebraic.elimination",
                "tried_families": [
                    {
                        "route_id": "route_001",
                        "family": "crypto.lattice.small_roots",
                        "status": "cut",
                        "cut_reason": "helper_bound_limit",
                        "failure_type": "helper_bound_limit",
                        "failure_signals": ["bound certificate negative"],
                        "ended_at_cycle": 2,
                        "started_at_cycle": 0,
                        "experiments": [],
                    }
                ],
                "failure_type": "helper_bound_limit",
                "failure_signals": ["bound certificate negative"],
            }
        )

        route = sup._load_route_state(cstate)
        sup._apply_family_switch(cstate, route)
        cstate["route_control"] = route.to_dict()

        tried = cstate["route_control"]["tried_families"]
        old_entry = next(item for item in tried if item["family"] == "crypto.lattice.small_roots")
        self.assertEqual(old_entry["cut_reason"], "helper_bound_limit")
        self.assertEqual(old_entry["failure_type"], "helper_bound_limit")
        self.assertEqual(old_entry["failure_signals"], ["bound certificate negative"])
        self.assertEqual(old_entry["ended_at_cycle"], 2)
        # New family entry is created with the route-start contract.
        self.assertTrue(any(item["family"] == "crypto.algebraic.elimination" for item in tried))

    def test_persistent_lane_stop_report_idempotent_across_steps_and_restart(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        existing_lane = dict(cstate["route_control"]["persistent_lane"])
        cstate["route_control"].update(
            {
                "current_family": "crypto.algebraic.elimination",
                "route_phase": "exhausted",
                "tried_families": [
                    {
                        "route_id": "route_001",
                        "family": "crypto.algebraic.elimination",
                        "status": "exhausted",
                        "started_at_cycle": 0,
                        "ended_at_cycle": 3,
                        "experiments": [],
                        "failure_type": "evidence_insufficient",
                        "failure_signals": ["all gates closed"],
                        "cut_reason": "exhausted",
                        "exhaustion_reason": "lane stopped with no remaining blockers",
                    }
                ],
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "stale",
                "persistent_lane": {
                    **existing_lane,
                    "status": "stale",
                    "no_candidate_blockers": [],
                    "negative_evidence": [],
                    "stop_report_path": None,
                },
                "candidate_queue_empty": True,
                "local_baseline_done": True,
                "short_codex_done": True,
                "family_switch_done": True,
                "failure_type": "evidence_insufficient",
                "no_candidate_blockers": [],
            }
        )
        cstate["route_control_action_state"]["family_switch"].update(
            {
                "status": "complete",
                "from_family": "crypto.lattice.small_roots",
                "to_family": "crypto.algebraic.elimination",
                "switched_at": "2026-05-10T00:00:00+00:00",
            }
        )
        queue_path = self.root / "artifacts" / "challenges" / "11" / "codex_candidates.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("[]", encoding="utf-8")
        stop_report_path = self.root / "artifacts" / "challenges" / "11" / "persistent_lane_stop_report.json"
        stop_report_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "stop_reason": "no remaining route questions",
                    "exhausted_families": ["crypto.algebraic.elimination"],
                    "remaining_blockers": [],
                    "no_candidate_allowed": True,
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        cstate = sup.state["challenges"]["11"]
        first_negative = list(cstate["route_control"]["persistent_lane"]["negative_evidence"])
        first_blockers = list(cstate["route_control"]["no_candidate_blockers"])
        consumed_hash = cstate["route_control_action_state"]["persistent_lane"].get("consumed_stop_report_hash")
        self.assertIn("no remaining route questions", first_negative)
        self.assertTrue(consumed_hash, "stop_report consumption must record an idempotency hash")

        sup.step_challenge({"id": 11, "category": "Crypto"})

        second_negative = list(cstate["route_control"]["persistent_lane"]["negative_evidence"])
        second_blockers = list(cstate["route_control"]["no_candidate_blockers"])
        self.assertEqual(
            first_negative,
            second_negative,
            "negative_evidence must not be re-appended on a second step over the same stop report",
        )
        self.assertEqual(first_blockers, second_blockers)

        sup._save_state()
        adapter2 = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup2 = _make_supervisor(cfg=cfg, adapter=adapter2, agents={"crypto": lambda ch: None})
        cstate2 = sup2.state["challenges"]["11"]
        self.assertEqual(
            cstate2["route_control_action_state"]["persistent_lane"].get("consumed_stop_report_hash"),
            consumed_hash,
            "consumed_stop_report_hash must survive supervisor restart",
        )

        sup2.step_challenge({"id": 11, "category": "Crypto"})

        third_negative = list(cstate2["route_control"]["persistent_lane"]["negative_evidence"])
        self.assertEqual(
            first_negative,
            third_negative,
            "new supervisor over saved state must not re-append negative_evidence",
        )

    def test_persistent_lane_update_idempotent_across_steps(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        existing_lane = dict(cstate["route_control"]["persistent_lane"])
        cstate["route_control"].update(
            {
                "current_family": "crypto.algebraic.elimination",
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "active",
                "persistent_lane": {
                    **existing_lane,
                    "status": "active",
                    "negative_evidence": [],
                    "no_candidate_blockers": ["pending_blocker"],
                },
                "no_candidate_blockers": [
                    "persistent_lane_active",
                    "persistent_lane_blockers",
                ],
            }
        )
        update_path = self.root / "artifacts" / "challenges" / "11" / "persistent_lane_update.json"
        update_path.parent.mkdir(parents=True, exist_ok=True)
        update_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "open_questions": [],
                    "alternative_families": [],
                    "public_search_ledger": [],
                    "helper_evaluation": [],
                    "negative_evidence": ["lane finished cleanly"],
                    "no_candidate_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        cstate = sup.state["challenges"]["11"]
        consumed_hash = cstate["route_control_action_state"]["persistent_lane"].get("consumed_update_hash")
        self.assertTrue(consumed_hash)
        first_lane_status = cstate["route_control"]["persistent_lane_status"]
        first_lane_state = dict(cstate["route_control"]["persistent_lane"])

        sup.step_challenge({"id": 11, "category": "Crypto"})

        cstate = sup.state["challenges"]["11"]
        self.assertEqual(
            cstate["route_control_action_state"]["persistent_lane"].get("consumed_update_hash"),
            consumed_hash,
        )
        self.assertEqual(cstate["route_control"]["persistent_lane_status"], first_lane_status)
        # The persistent_lane sub-state itself is the idempotency target;
        # higher-level route blockers may evolve from independent
        # route-control bookkeeping (e.g., same-family stall counters)
        # and are not part of this contract.
        self.assertEqual(
            cstate["route_control"]["persistent_lane"]["negative_evidence"],
            first_lane_state["negative_evidence"],
        )
        self.assertEqual(
            cstate["route_control"]["persistent_lane"]["no_candidate_blockers"],
            first_lane_state["no_candidate_blockers"],
        )

    def test_pending_submit_survives_supervisor_restart_without_resubmitting(self):
        cfg = _make_cfg(self.root)
        adapter1 = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending"]},
        )
        flag = "flag{cross-restart-pending-then-accept}"

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=flag)

        sup1 = _make_supervisor(cfg=cfg, adapter=adapter1, agents={"misc": agent})
        sup1.sync_challenges()
        sup1.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(len(adapter1.submit_calls), 1)
        cstate1 = sup1.state["challenges"]["11"]
        self.assertEqual(cstate1["state"], sup_mod.CHALLENGE_STATE_PENDING)
        self.assertIn("pending_record_payload", cstate1)
        self.assertIn("flag_redacted", cstate1["pending_record_payload"])
        self.assertEqual(sup1.guard.state_store.wrong_count("11"), 0)
        sup1._save_state()

        adapter2 = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )
        sup2 = _make_supervisor(cfg=cfg, adapter=adapter2, agents={"misc": agent})
        cstate2 = sup2.state["challenges"]["11"]
        self.assertEqual(cstate2["state"], sup_mod.CHALLENGE_STATE_PENDING)
        self.assertIn("pending_record_payload", cstate2)
        # The redacted payload survived persistence; no plaintext flag.
        state_text = sup2.state_path.read_text(encoding="utf-8")
        self.assertNotIn(flag, state_text)

        sup2.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(adapter2.submit_calls, [], "pending state must not trigger a second submit on restart")
        self.assertEqual(len(adapter2.poll_calls), 1, "exactly one poll resolves the pending submit")
        cstate2 = sup2.state["challenges"]["11"]
        self.assertEqual(cstate2["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)
        self.assertNotIn("pending_record_payload", cstate2)
        # Guard state reflects the accepted outcome via the pending poll path.
        self.assertEqual(sup2.guard.state_store.wrong_count("11"), 0)
        snap = sup2.guard.state_store.snapshot()
        submits = snap["challenges"].get("11", {}).get("submits") or []
        self.assertGreater(len(submits), 0)
        self.assertTrue(submits[-1]["correct"])

    def test_codex_candidate_transits_flag_guard_before_adapter(self):
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )

        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        evidence_dir = artifacts_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "strings.txt").write_text(
            "...\nflag{codex-must-pass-through-guard}\n", encoding="utf-8"
        )
        codex_flag = "flag{codex-must-pass-through-guard}"
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([
                {
                    "challenge_id": "11",
                    "candidate": codex_flag,
                    "confidence": "high",
                    "evidence_paths": ["artifacts/challenges/11/evidence/strings.txt"],
                    "submit_recommendation": "never_direct_submit",
                    "notes": "high-confidence binwalk hit",
                }
            ]),
            encoding="utf-8",
        )

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        # Adapter saw the flag exactly once.
        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(adapter.submit_calls[0][2], codex_flag)

        # Guard's state_store recorded the candidate's outcome — proves
        # the candidate was routed through FlagGuard.record_outcome
        # rather than the supervisor calling adapter directly.
        snap = sup.guard.state_store.snapshot()
        ch_record = snap["challenges"].get("11", {})
        submits = ch_record.get("submits") or []
        self.assertGreater(len(submits), 0, "FlagGuard.state_store must record the Codex submit")
        self.assertTrue(submits[-1]["correct"])

        # JSONL log carries the guard_decision event with the
        # AUTO_SUBMIT action — the canonical proof that
        # supervisor -> validator -> FlagGuard -> adapter was honored.
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn("codex_candidate_received", log_text)
        guard_decision_lines = [
            line for line in log_text.splitlines() if '"event_type": "guard_decision"' in line
        ]
        self.assertTrue(guard_decision_lines, "guard_decision event missing — Guard never saw Codex candidate")
        self.assertTrue(any('"auto_submit"' in line for line in guard_decision_lines))
        # Plaintext flag still must not leak into the log.
        for line in guard_decision_lines:
            self.assertNotIn(codex_flag, line)

    def test_expert_review_cut_route_writes_cut_ledger_and_keeps_family(self):
        cfg = _make_cfg(self.root)
        cfg["agent"]["enabled_categories"] = ["crypto"]
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "current_family": "crypto.lattice.small_roots",
                "public_search_status": "complete",
                "expert_review_status": "running",
                "route_decision": "spawn_expert_review",
                "no_candidate_blockers": ["expert_review_required"],
            }
        )

        result_path = self.root / "artifacts" / "challenges" / "11" / "expert_review_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "verdict": "cut_route",
                    "failure_class": "wrong_target",
                    "continue_current_family": False,
                    "next_families": [],
                    "first_experiment": {
                        "description": "must be dropped: route is being cut",
                        "lane": "main",
                    },
                    "stop_condition": "no further family viable",
                    "no_candidate_blockers": ["no_alternate_family"],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        route = cstate["route_control"]
        self.assertEqual(route["current_family"], "crypto.lattice.small_roots")
        self.assertEqual(route["route_phase"], "cut")
        self.assertNotEqual(route["route_phase"], "exhausted")
        self.assertNotIn("cut_route", route["pending_actions"])

        tried = route["tried_families"]
        families = [entry["family"] for entry in tried]
        self.assertEqual(families.count("crypto.lattice.small_roots"), 1)
        entry = next(item for item in tried if item["family"] == "crypto.lattice.small_roots")
        self.assertEqual(entry["status"], "cut")
        self.assertEqual(entry["failure_type"], "wrong_target")
        self.assertEqual(entry["cut_reason"], "wrong_target")
        self.assertIsNotNone(entry["ended_at_cycle"])

        for item in tried:
            for experiment in item["experiments"]:
                self.assertNotIn(
                    "must be dropped: route is being cut",
                    experiment.get("description", ""),
                )

        self.assertIn("no_alternate_family", route["no_candidate_blockers"])

    def test_persistent_lane_request_enters_durable_route_state(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        route = sup.state["challenges"]["11"]["route_control"]
        route.update(
            {
                "current_family": "crypto.algebraic.elimination",
                "route_phase": "exhausted",
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "not_started",
                "candidate_queue_empty": True,
                "local_baseline_done": True,
                "short_codex_done": True,
                "family_switch_done": True,
                "failure_type": "evidence_insufficient",
                "no_candidate_blockers": [],
            }
        )
        queue_path = self.root / "artifacts" / "challenges" / "11" / "codex_candidates.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("[]", encoding="utf-8")

        sup.step_challenge({"id": 11, "category": "Crypto"})

        lane_path = self.root / "artifacts" / "challenges" / "11" / "persistent_lane_request.json"
        self.assertTrue(lane_path.exists())
        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        action_state = cstate["route_control_action_state"]["persistent_lane"]
        self.assertEqual(route["route_decision"], "spawn_persistent_lane")
        self.assertEqual(route["persistent_lane_status"], "active")
        self.assertNotIn("spawn_persistent_lane", route["pending_actions"])
        self.assertEqual(action_state["status"], "active")
        self.assertTrue(action_state["request_path"].endswith("persistent_lane_request.json"))

    def test_persistent_lane_stop_report_is_consumed_before_no_candidate(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        route.update(
            {
                "current_family": "crypto.algebraic.elimination",
                "route_phase": "exhausted",
                "tried_families": [
                    {
                        "route_id": "route_001",
                        "family": "crypto.algebraic.elimination",
                        "status": "exhausted",
                        "reason": "all route gates closed",
                        "started_at_cycle": 0,
                        "ended_at_cycle": 3,
                        "experiments": [],
                        "failure_type": "evidence_insufficient",
                        "failure_signals": ["all route-control gates exhausted"],
                        "cut_reason": "exhausted",
                        "exhaustion_reason": "persistent lane stopped with no remaining blockers",
                    }
                ],
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "stale",
                "persistent_lane": {
                    **route["persistent_lane"],
                    "status": "stale",
                    "no_candidate_blockers": ["open algebra boundary"],
                    "stop_report_path": None,
                },
                "candidate_queue_empty": True,
                "local_baseline_done": True,
                "short_codex_done": True,
                "family_switch_done": True,
                "failure_type": "evidence_insufficient",
                "no_candidate_blockers": [],
            }
        )
        cstate["route_control_action_state"]["family_switch"].update(
            {
                "status": "complete",
                "from_family": "crypto.lattice.small_roots",
                "to_family": "crypto.algebraic.elimination",
                "switched_at": "2026-05-10T00:00:00+00:00",
            }
        )
        queue_path = self.root / "artifacts" / "challenges" / "11" / "codex_candidates.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("[]", encoding="utf-8")

        sup.step_challenge({"id": 11, "category": "Crypto"})

        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_NO_AGENT)
        self.assertIn(
            "persistent_lane_stop_report_required",
            cstate["route_control"]["no_candidate_blockers"],
        )

        stop_report_path = self.root / "artifacts" / "challenges" / "11" / "persistent_lane_stop_report.json"
        stop_report_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "stop_reason": "no remaining route questions",
                    "exhausted_families": ["crypto.algebraic.elimination"],
                    "remaining_blockers": [],
                    "no_candidate_allowed": True,
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        route = cstate["route_control"]
        self.assertEqual(route["persistent_lane_status"], "complete")
        self.assertEqual(route["persistent_lane"]["no_candidate_blockers"], [])
        self.assertTrue(route["persistent_lane"]["stop_report_path"].endswith("persistent_lane_stop_report.json"))
        self.assertEqual(cstate["route_control_action_state"]["persistent_lane"]["status"], "complete")
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_NO_CANDIDATE)

    def test_invalid_persistent_lane_stop_report_does_not_unblock(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"crypto": lambda ch: None})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        cstate["route_control"].update(
            {
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "stopped",
                "persistent_lane": {
                    **cstate["route_control"]["persistent_lane"],
                    "status": "stopped",
                    "stop_report_path": None,
                },
                "route_phase": "exhausted",
                "tried_families": [
                    {
                        "route_id": "route_001",
                        "family": "crypto.initial",
                        "status": "exhausted",
                        "ended_at_cycle": 2,
                        "failure_type": "evidence_insufficient",
                        "failure_signals": ["all gates closed"],
                        "exhaustion_reason": "all route gates closed",
                    }
                ],
                "candidate_queue_empty": True,
                "local_baseline_done": True,
                "short_codex_done": True,
                "family_switch_done": True,
                "failure_type": "evidence_insufficient",
                "no_candidate_blockers": [],
            }
        )
        queue_path = self.root / "artifacts" / "challenges" / "11" / "codex_candidates.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("[]", encoding="utf-8")
        stop_report_path = self.root / "artifacts" / "challenges" / "11" / "persistent_lane_stop_report.json"
        stop_report_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "stop_reason": "missing explicit allowance",
                    "exhausted_families": ["crypto.initial"],
                    "remaining_blockers": [],
                }
            ),
            encoding="utf-8",
        )

        sup.step_challenge({"id": 11, "category": "Crypto"})

        self.assertEqual(cstate["route_control"]["persistent_lane_status"], "stopped")
        self.assertIn(
            "persistent_lane_stop_report_required",
            cstate["route_control"]["no_candidate_blockers"],
        )
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_NO_AGENT)

    def test_switch_family_pending_action_updates_route_state(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "Crypto", "category": "Crypto"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        cstate = sup.state["challenges"]["11"]
        route = cstate["route_control"]
        route.update(
            {
                "current_family": "crypto.lattice.small_roots",
                "next_family": "crypto.algebraic.elimination",
                "public_search_status": "complete",
                "persistent_lane_status": "active",
            }
        )

        sup._route_progress(
            cstate,
            cid="11",
            category="crypto",
            progress=sup_mod.ProgressType.NO_PROGRESS,
            failure_type=sup_mod.FailureType.HELPER_BOUND_LIMIT,
            failure_signals=["real instance outside helper math coverage"],
            reason="helper_bound_limit",
        )

        route = cstate["route_control"]
        action_state = cstate["route_control_action_state"]["family_switch"]
        self.assertEqual(route["current_family"], "crypto.algebraic.elimination")
        self.assertIsNone(route["next_family"])
        self.assertTrue(route["family_switch_done"])
        self.assertNotIn("switch_family", route["pending_actions"])
        self.assertEqual(action_state["status"], "complete")
        self.assertEqual(action_state["from_family"], "crypto.lattice.small_roots")
        self.assertEqual(action_state["to_family"], "crypto.algebraic.elimination")

    def test_exhausted_route_allows_no_candidate_state(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        route = sup.state["challenges"]["11"]["route_control"]
        route.update(
            {
                "route_phase": "exhausted",
                "tried_families": [
                    {
                        "route_id": "route_001",
                        "family": "misc.initial",
                        "status": "exhausted",
                        "reason": "test certificate",
                        "started_at_cycle": 0,
                        "ended_at_cycle": 1,
                        "experiments": [],
                        "failure_type": "evidence_insufficient",
                        "failure_signals": ["public search, expert review, and persistent lane exhausted"],
                        "cut_reason": "exhausted",
                        "exhaustion_reason": "all required route-control gates completed with no candidate",
                    }
                ],
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "complete",
                "candidate_queue_empty": True,
                "local_baseline_done": True,
                "short_codex_done": True,
                "family_switch_done": True,
                "failure_type": "evidence_insufficient",
                "no_candidate_blockers": [],
            }
        )
        sup.state["challenges"]["11"]["route_control_action_state"]["family_switch"].update(
            {
                "status": "complete",
                "from_family": "misc.initial",
                "to_family": "misc.alt",
                "switched_at": "2026-05-10T00:00:00+00:00",
            }
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        cstate = sup.state["challenges"]["11"]
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_NO_CANDIDATE)
        self.assertEqual(cstate["route_control"]["route_decision"], "allow_no_candidate")

    def test_no_candidate_rejects_handwritten_family_switch_done(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        route = sup.state["challenges"]["11"]["route_control"]
        route.update(
            {
                "route_phase": "exhausted",
                "tried_families": [
                    {
                        "route_id": "route_001",
                        "family": "misc.initial",
                        "status": "exhausted",
                        "reason": "test certificate",
                        "started_at_cycle": 0,
                        "ended_at_cycle": 1,
                        "experiments": [],
                        "failure_type": "evidence_insufficient",
                        "failure_signals": ["all route-control gates exhausted"],
                        "cut_reason": "exhausted",
                        "exhaustion_reason": "all required route-control gates completed with no candidate",
                    }
                ],
                "public_search_status": "complete",
                "expert_review_status": "complete",
                "persistent_lane_status": "complete",
                "candidate_queue_empty": True,
                "local_baseline_done": True,
                "short_codex_done": True,
                "family_switch_done": True,
                "failure_type": "evidence_insufficient",
                "no_candidate_blockers": [],
            }
        )

        sup.step_challenge({"id": 11, "category": "Misc"})

        cstate = sup.state["challenges"]["11"]
        self.assertEqual(cstate["state"], sup_mod.CHALLENGE_STATE_NO_AGENT)
        self.assertEqual(cstate["route_control"]["route_decision"], "block_no_candidate")
        self.assertFalse(cstate["route_control"]["family_switch_done"])
        self.assertIn("family_switch_required", cstate["route_control"]["no_candidate_blockers"])

    # ---- 10. category not in enabled list → no_agent ----------

    def test_unsupported_category_marked_no_agent(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 22, "title": "Pwn1", "category": "Pwn"}])
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        sup.step_challenge({"id": 22, "category": "Pwn"})
        self.assertEqual(adapter.submit_calls, [])
        self.assertEqual(sup.state["challenges"]["22"]["state"], sup_mod.CHALLENGE_STATE_NO_AGENT)
        route = sup.state["challenges"]["22"]["route_control"]
        self.assertEqual(route["current_family"], "pwn.initial")
        self.assertEqual(route["route_decision"], "spawn_public_search")
        self.assertIn("public_search_required", route["no_candidate_blockers"])

    # ---- 11. dict-by-category challenges (real GZCTF shape) ----

    def test_sync_handles_dict_by_category_challenges(self):
        # Real GameDetailModel.challenges is Dictionary<string, ChallengeInfo[]>
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[
                {"id": 11, "title": "Misc 1", "category": "Misc"},
                {"id": 12, "title": "Misc 2", "category": "Misc"},
                {"id": 31, "title": "Web 1", "category": "Web"},
            ],
            challenges_shape="dict",
        )
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        self.assertEqual(
            set(sup.state["challenges"].keys()), {"11", "12", "31"},
            "dict-by-category challenges must flatten into individual entries"
        )
        self.assertEqual(sup.state["challenges"]["11"]["category"], "misc")
        self.assertEqual(sup.state["challenges"]["31"]["category"], "web")

    def test_normalize_challenges_helper(self):
        flat = sup_mod.normalize_challenges(
            {"challenges": [{"id": 1, "category": "Misc"}, {"id": 2}]}
        )
        self.assertEqual(len(flat), 2)
        keyed = sup_mod.normalize_challenges(
            {"challenges": {"Misc": [{"id": 11}], "Web": [{"id": 21, "category": "Web"}]}}
        )
        self.assertEqual({c["id"] for c in keyed}, {11, 21})
        # category back-fill from key when entry lacks it
        cat_by_id = {c["id"]: c["category"] for c in keyed}
        self.assertEqual(cat_by_id[11], "Misc")
        self.assertEqual(cat_by_id[21], "Web")
        # malformed shapes degrade gracefully
        self.assertEqual(sup_mod.normalize_challenges({}), [])
        self.assertEqual(sup_mod.normalize_challenges({"challenges": "garbage"}), [])
        self.assertEqual(sup_mod.normalize_challenges({"challenges": None}), [])

    # ---- 12. pending → never produces a second submit ----------

    def test_pending_to_wrong_updates_guard_state(self):
        # Codex review §1: pending→wrong must increment guard's
        # wrong_count and (under AI-identity max_wrong=1) flip
        # frozen=True.  Without this, the supervisor's local
        # wrong_frozen state and the guard's state diverge, and any
        # later submit attempt for the same challenge would not be
        # blocked at the guard layer.
        cfg = _make_cfg(self.root, max_wrong=1)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending", "wrong"]},
        )

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag="flag{wrong-on-platform}")

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()

        # Before any submit: guard state is fresh
        self.assertEqual(sup.guard.state_store.wrong_count("11"), 0)
        self.assertFalse(sup.guard.state_store.is_frozen("11"))

        sup.step_challenge({"id": 11, "category": "Misc"})  # submit, pending
        # Submit happened but platform still pending — guard should NOT
        # have incremented wrong_count yet.
        self.assertEqual(sup.guard.state_store.wrong_count("11"), 0)
        self.assertEqual(
            sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_PENDING
        )

        sup.step_challenge({"id": 11, "category": "Misc"})  # poll, terminalises wrong
        # Now both stores must be consistent
        self.assertEqual(
            sup.state["challenges"]["11"]["state"],
            sup_mod.CHALLENGE_STATE_WRONG_FROZEN,
        )
        self.assertEqual(
            sup.guard.state_store.wrong_count("11"), 1,
            "guard wrong_count must reflect the platform's wrong verdict"
        )
        self.assertTrue(
            sup.guard.state_store.is_frozen("11"),
            "guard must mark challenge frozen since max_wrong_per_challenge=1"
        )

    def test_pending_to_accepted_records_outcome_in_guard(self):
        # Codex review §1 mirror: pending→accepted should also be
        # recorded so that guard's submit history reflects the final
        # correct=True outcome (used by post-game audit + writeup).
        cfg = _make_cfg(self.root, max_wrong=1)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending", "accepted"]},
        )

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag="flag{eventually-accepted}")

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})  # submit, pending
        sup.step_challenge({"id": 11, "category": "Misc"})  # poll → accepted

        self.assertEqual(
            sup.state["challenges"]["11"]["state"],
            sup_mod.CHALLENGE_STATE_ACCEPTED,
        )
        # No wrong_count increment; guard records the acceptance through
        # state_store with correct=True.
        self.assertEqual(sup.guard.state_store.wrong_count("11"), 0)
        # Two records: initial pending submit (correct=None, anchors
        # the rate-limit window) + the terminal accept (correct=True,
        # written when the pending poll resolved).  This is the
        # expected shape — the rate-limit clock starts at submit time,
        # not at terminalisation.
        snap = sup.guard.state_store.snapshot()
        ch = snap["challenges"].get("11", {})
        submits = ch.get("submits") or []
        self.assertEqual(len(submits), 2)
        self.assertIsNone(submits[0]["correct"])
        self.assertTrue(submits[-1]["correct"])

    def test_pending_state_does_not_persist_plaintext_flag(self):
        # Codex review §2 hygiene fix: the supervisor's pending payload
        # must not write the plaintext flag into state/ai_contest_state.json.
        cfg = _make_cfg(self.root, max_wrong=1)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending"]},
        )
        the_flag = "flag{plaintext-must-not-leak-to-state-file}"

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=the_flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        state_text = sup.state_path.read_text(encoding="utf-8")
        self.assertNotIn(
            the_flag, state_text,
            "plaintext flag must NOT appear in state file"
        )
        # But the redacted shape should be present in pending_record_payload
        self.assertIn("flag{p", state_text)  # redacted prefix
        self.assertIn("flag_hash", state_text)

    # ---- 13. Codex sidecar ingest (gated by config) -----------

    def test_codex_ingest_disabled_by_default_skips_artifacts(self):
        # No cfg.codex_sidecar.enabled → supervisor never reads
        # artifacts/.../codex_candidates.json, even when one exists.
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={},  # any submit would error
        )

        # Seed a Codex candidate file the supervisor would otherwise pick up
        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": "flag{codex-must-not-fire-when-disabled}",
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/x.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "would be auto-submitted if ingest were on",
            }]),
            encoding="utf-8",
        )

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(
            adapter.submit_calls, [],
            "sidecar disabled by default; Codex candidate must not have been used"
        )

    def test_codex_ingest_enabled_uses_validated_candidate(self):
        # cfg.codex_sidecar.enabled = True + valid candidate → supervisor
        # builds a FlagCandidate from it and calls guard/submit.
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )

        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        evidence_dir = artifacts_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_file = evidence_dir / "strings.txt"
        evidence_file.write_text("...\nflag{codex-sidecar-routed-via-guard}\n", encoding="utf-8")
        codex_flag = "flag{codex-sidecar-routed-via-guard}"
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": codex_flag,
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/strings.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "high-confidence binwalk hit",
            }]),
            encoding="utf-8",
        )

        # Built-in agent returns nothing, so only the Codex path can produce a
        # candidate.
        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(adapter.submit_calls[0][2], codex_flag)
        self.assertEqual(
            sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED
        )

    def test_codex_ingest_rejects_invalid_candidate_falls_to_internal(self):
        # cfg.codex_sidecar.enabled = True but Codex output fails the
        # validator (forbidden key) → drop with codex_candidate_rejected
        # event and let the internal agent take over.
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )

        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": "flag{codex-with-forbidden-submit-key}",
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/x.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "trying to bypass guard",
                "submit": "POST /api/...",  # forbidden top-level key
            }]),
            encoding="utf-8",
        )

        internal_flag = "flag{internal-agent-took-over}"

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=internal_flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(
            adapter.submit_calls[0][2], internal_flag,
            "forbidden Codex candidate must be rejected; internal agent's flag wins"
        )

    def test_guard_decision_log_redacts_plaintext_flag(self):
        # Codex review §1: the JSONL log file must never carry a
        # plaintext flag.  GuardDecision.to_dict() includes the flag,
        # so the supervisor must sanitize before emitting the
        # guard_decision event.
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )
        the_flag = "flag{must-not-appear-in-jsonl-log-line}"

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=the_flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn("guard_decision", log_text)
        self.assertNotIn(the_flag, log_text, "plaintext flag must not be in JSONL log")
        # Sanitized markers should be present
        self.assertIn("flag_redacted", log_text)
        self.assertIn("flag_hash", log_text)

    def test_codex_received_log_does_not_contain_notes_excerpt(self):
        # Codex review §1: codex_candidate_received event must not
        # carry any portion of `notes` because Codex notes can include
        # the raw flag.  Only metadata (confidence + counts) is allowed.
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )

        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        evidence_dir = artifacts_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "strings.txt").write_text("evidence body", encoding="utf-8")
        notes_with_flag = (
            "Strings dump found flag{codex-notes-may-leak-flag} at "
            "offset 0x1a40."
        )
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": "flag{codex-notes-may-leak-flag}",
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/strings.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": notes_with_flag,
            }]),
            encoding="utf-8",
        )

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": lambda ch: None})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn("codex_candidate_received", log_text)
        # The notes string itself must NOT appear anywhere in the log.
        # (The flag itself will appear elsewhere — in submit calls — but
        # it must not appear in the codex_candidate_received line.)
        for line in log_text.splitlines():
            if '"event_type": "codex_candidate_received"' in line:
                self.assertNotIn(
                    "codex-notes-may-leak-flag", line,
                    "codex_candidate_received must not carry any part of notes"
                )
                self.assertNotIn(
                    "offset 0x1a40", line,
                    "codex_candidate_received must not carry any notes excerpt"
                )

    def test_codex_high_confidence_with_missing_evidence_rejected(self):
        # Codex review §2: schema-valid + confidence=high but the
        # declared evidence path does NOT exist on disk → reject.
        # Internal agent must take over so the challenge isn't starved.
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )

        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        # NOTE: deliberately do NOT create the evidence file.
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": "flag{codex-hallucinated-no-real-evidence}",
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/missing.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "claims a tool output exists but it doesn't",
            }]),
            encoding="utf-8",
        )

        internal_flag = "flag{internal-agent-took-over-after-hallucination}"

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=internal_flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        # The Codex candidate must NOT have been submitted, and the
        # internal agent's flag wins.
        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(adapter.submit_calls[0][2], internal_flag)
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn("codex_candidate_rejected", log_text)
        self.assertIn("evidence_missing", log_text)
        self.assertNotIn("codex-hallucinated-no-real-evidence", log_text)

    def test_capability_matrix_refuses_start_when_required_capability_missing(self):
        # AI identity has no human reviewer to mediate a demoted
        # category, so missing required capabilities for a category in
        # auto_submit_categories must be a hard startup failure rather
        # than a silent demotion to HUMAN_REVIEW.
        self._write_runtime_capabilities(self._base_runtime_capabilities(
            crypto_lattice=False,
            crypto_classic=False,
        ))
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "Crypto 1", "category": "Crypto"}],
            submit_outcomes={},
        )

        with self.assertRaises(RuntimeError) as cm:
            _make_supervisor(cfg=cfg, adapter=adapter, agents={})

        self.assertIn("crypto", str(cm.exception))
        # The category_capability_missing event must be emitted before
        # the supervisor refuses, so the operator can see exactly which
        # capability is missing without parsing the exception message.
        log_files = list((self.root / "logs").rglob("*.jsonl"))
        self.assertTrue(log_files, "supervisor must emit a JSONL log even when refusing to start")
        log_text = "\n".join(p.read_text(encoding="utf-8") for p in log_files)
        self.assertIn("category_capability_missing", log_text)
        self.assertIn('"category": "crypto"', log_text)
        # Adapter never sees a submit because the supervisor never started.
        self.assertEqual(adapter.submit_calls, [])

    def test_capability_matrix_keeps_crypto_when_only_lattice_missing(self):
        self._write_runtime_capabilities(self._base_runtime_capabilities(
            crypto_lattice=False,
            crypto_classic=True,
        ))
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "Crypto 1", "category": "Crypto"}],
            submit_outcomes={"11": ["accepted"]},
        )
        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        evidence_dir = artifacts_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "probe.txt").write_text("evidence", encoding="utf-8")
        candidate = "flag{crypto-lattice-missing-but-classic-ok}"
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": candidate,
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/probe.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "classic crypto is enough to keep auto-submit",
            }]),
            encoding="utf-8",
        )

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Crypto"})

        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(adapter.submit_calls[0][2], candidate)
        self.assertEqual(sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)

    def test_capability_matrix_missing_file_does_not_crash_supervisor(self):
        self._write_runtime_capabilities(None)
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "Crypto 1", "category": "Crypto"}],
            submit_outcomes={"11": ["accepted"]},
        )
        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        evidence_dir = artifacts_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "probe.txt").write_text("evidence", encoding="utf-8")
        candidate = "flag{cap-file-missing-fail-open}"
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": candidate,
                "confidence": "high",
                "evidence_paths": ["artifacts/challenges/11/evidence/probe.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "missing capability file must fail open",
            }]),
            encoding="utf-8",
        )

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Crypto"})

        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(adapter.submit_calls[0][2], candidate)
        self.assertEqual(sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)
        self.assertFalse((self.root / "state" / "runtime_capabilities.json").exists())

    def test_capability_matrix_emits_category_capability_missing_event_for_web(self):
        # Same hard-fail contract as the crypto case, but for web.
        # Confirms the event payload identifies the offending category.
        self._write_runtime_capabilities(self._base_runtime_capabilities(
            web_static=False,
        ))
        cfg = _make_cfg(self.root)
        cfg["submit"]["auto_submit_categories"].append("web")
        adapter = _MockAdapter(
            challenges=[{"id": 22, "title": "Web1", "category": "Web"}],
            submit_outcomes={},
        )

        with self.assertRaises(RuntimeError) as cm:
            _make_supervisor(cfg=cfg, adapter=adapter, agents={})

        self.assertIn("web", str(cm.exception))
        log_files = list((self.root / "logs").rglob("*.jsonl"))
        self.assertTrue(log_files)
        log_text = "\n".join(p.read_text(encoding="utf-8") for p in log_files)
        self.assertIn("category_capability_missing", log_text)
        self.assertIn('"category": "web"', log_text)
        self.assertEqual(adapter.submit_calls, [])

    def test_codex_low_confidence_does_not_block_internal_agent(self):
        # Codex review §3: a valid but confidence=low Codex candidate
        # must NOT be treated as a submit candidate.  It is logged as
        # advisory and the internal agent runs as if Codex hadn't
        # spoken — otherwise a single low-confidence Codex output
        # could indefinitely starve the Misc / Forensics agents.
        cfg = _make_cfg(self.root)
        cfg["codex_sidecar"] = {"enabled": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )

        artifacts_dir = self.root / "artifacts" / "challenges" / "11"
        evidence_dir = artifacts_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "strings.txt").write_text("evidence body", encoding="utf-8")
        (artifacts_dir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "11",
                "candidate": "flag{codex-low-confidence-must-not-fire}",
                "confidence": "low",
                "evidence_paths": ["artifacts/challenges/11/evidence/strings.txt"],
                "submit_recommendation": "never_direct_submit",
                "notes": "single tool, no corroboration",
            }]),
            encoding="utf-8",
        )

        internal_flag = "flag{internal-agent-not-blocked-by-low-codex}"

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=internal_flag)

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})

        self.assertEqual(len(adapter.submit_calls), 1)
        self.assertEqual(adapter.submit_calls[0][2], internal_flag)
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn("codex_candidate_advisory_only", log_text)

    def test_pending_blocks_new_candidate_even_if_different_flag(self):
        # Codex review: under AI identity rules, FlagSubmitted/pending
        # is a first-class state.  The supervisor must NOT call the
        # agent or submit a different flag while a previous submit_id
        # is still in flight.  It only polls until terminal.
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["pending", "pending", "accepted"]},
        )

        # Each tick the agent produces a DIFFERENT flag — without the
        # pending gate, the supervisor would happily submit each one.
        flag_seq = iter([
            "flag{first-candidate-pending-strong}",
            "flag{second-candidate-different}",
            "flag{third-candidate-yet-another}",
        ])

        def agent(challenge):
            return _high_conf_candidate(challenge.id, flag=next(flag_seq))

        sup = _make_supervisor(cfg=cfg, adapter=adapter, agents={"misc": agent})
        sup.sync_challenges()
        sup.step_challenge({"id": 11, "category": "Misc"})  # tick 1: submit, pending
        sup.step_challenge({"id": 11, "category": "Misc"})  # tick 2: still pending → poll only
        sup.step_challenge({"id": 11, "category": "Misc"})  # tick 3: poll resolves accepted

        self.assertEqual(
            len(adapter.submit_calls), 1,
            "pending state must block any subsequent submit, even with a different agent flag"
        )
        # And the polls happened on tick 2 + tick 3 (exactly 2)
        self.assertEqual(len(adapter.poll_calls), 2)
        self.assertEqual(sup.state["challenges"]["11"]["state"], sup_mod.CHALLENGE_STATE_ACCEPTED)

    def test_human_review_guard_decision_writes_preview_notification_event(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(challenges=[{"id": 11, "title": "M", "category": "Misc"}])
        sup = _make_supervisor(
            cfg=cfg,
            adapter=adapter,
            agents={
                "misc": lambda ch: FlagCandidate(
                    challenge_id=ch.id,
                    flag="flag{needs-review-not-full-log}",
                    category="misc",
                    evidence_count=2,
                    extraction_confidence=0.75,
                    agent_votes=["flag{needs-review-not-full-log}"],
                )
            },
        )
        sup.sync_challenges()

        with patch.object(sup_mod, "notify_decision", create=True) as notify_decision:
            notify_decision.return_value = {
                "event": "human_review",
                "sent": False,
                "preview": "[DLUT-CTF] AI-safe review",
            }
            sup.step_challenge({"id": 11, "category": "Misc"})

        notify_decision.assert_called_once()
        self.assertEqual(notify_decision.call_args.args[0], {"enabled": False})
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn('"event_type": "notification"', log_text)
        self.assertIn('"event": "human_review"', log_text)
        self.assertIn('"sent": false', log_text)
        self.assertNotIn("flag{needs-review-not-full-log}", log_text)
        self.assertEqual(adapter.submit_calls, [])

    def test_wrong_answer_freeze_writes_preview_notification_event(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["wrong"]},
        )
        sup = _make_supervisor(
            cfg=cfg,
            adapter=adapter,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id, flag="flag{wrong-freeze-not-full-log}")},
        )
        sup.sync_challenges()

        with patch.object(sup_mod, "notify_submit_outcome", create=True) as notify_outcome:
            notify_outcome.return_value = {
                "event": "freeze",
                "sent": False,
                "preview": "[DLUT-CTF] freeze",
            }
            sup.step_challenge({"id": 11, "category": "Misc"})

        notify_outcome.assert_called_once()
        kwargs = notify_outcome.call_args.kwargs
        self.assertEqual(notify_outcome.call_args.args[0], {"enabled": False})
        self.assertTrue(kwargs["state_update"]["newly_frozen"])
        self.assertEqual(kwargs["max_wrong"], 1)
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn('"event_type": "notification"', log_text)
        self.assertIn('"event": "freeze"', log_text)
        self.assertNotIn("flag{wrong-freeze-not-full-log}", log_text)

    def test_accepted_writes_notification_when_enabled(self):
        cfg = _make_cfg(self.root)
        cfg["feishu"] = {"enabled": True, "notify_accepted": True}
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["accepted"]},
        )
        sup = _make_supervisor(
            cfg=cfg,
            adapter=adapter,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id, flag="flag{accepted-not-full-log}")},
        )
        sup.sync_challenges()

        with patch.object(sup_mod, "notify_submit_outcome", create=True) as notify_outcome:
            notify_outcome.return_value = {
                "event": "accepted",
                "sent": False,
                "preview": "[DLUT-CTF] accepted",
            }
            sup.step_challenge({"id": 11, "category": "Misc"})

        notify_outcome.assert_called_once()
        self.assertEqual(
            notify_outcome.call_args.args[0],
            {"enabled": True, "notify_accepted": True},
        )
        kwargs = notify_outcome.call_args.kwargs
        self.assertTrue(kwargs["state_update"]["accepted"])
        self.assertEqual(kwargs["state_update"]["platform_response"], "Accepted")
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn('"event_type": "notification"', log_text)
        self.assertIn('"event": "accepted"', log_text)
        self.assertNotIn("flag{accepted-not-full-log}", log_text)

    def test_cheat_detected_writes_kill_switch_notification_event(self):
        cfg = _make_cfg(self.root)
        adapter = _MockAdapter(
            challenges=[{"id": 11, "title": "M", "category": "Misc"}],
            submit_outcomes={"11": ["cheat"]},
        )
        sup = _make_supervisor(
            cfg=cfg,
            adapter=adapter,
            agents={"misc": lambda ch: _high_conf_candidate(ch.id, flag="flag{cheat-not-full-log}")},
        )
        sup.sync_challenges()

        with patch.object(sup_mod, "notify_kill_switch", create=True) as notify_kill:
            notify_kill.return_value = {
                "event": "kill_switch",
                "sent": False,
                "preview": "[DLUT-CTF] kill switch",
            }
            sup.step_challenge({"id": 11, "category": "Misc"})

        notify_kill.assert_called_once_with(
            {"enabled": False},
            activated=True,
            reason="CheatDetected",
        )
        log_text = sup.logger.path.read_text(encoding="utf-8")
        self.assertIn('"event_type": "notification"', log_text)
        self.assertIn('"event": "kill_switch"', log_text)
        self.assertNotIn("flag{cheat-not-full-log}", log_text)


if __name__ == "__main__":
    unittest.main()
