from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = PROJECT / "local" / "gzctf-lab" / "run_real_llm_solve.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("run_real_llm_solve", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


run_real_llm_solve = _load_script()


class _FakeStateStore:
    def __init__(self, snapshot: dict):
        self._snapshot = snapshot

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self._snapshot))


class _FakeSupervisor:
    def __init__(self, root: Path, snapshot: dict):
        self.guard = types.SimpleNamespace(state_store=_FakeStateStore(snapshot))
        self.state = {"challenges": snapshot.get("challenges", {})}
        self.logger = types.SimpleNamespace(
            path=root / "logs" / "local-gzctf-real-llm" / "fake-supervisor.jsonl"
        )
        self._ticks = 0

    def healthcheck(self) -> bool:
        return True

    def run_one_tick(self) -> None:
        self._ticks += 1


def _fake_boot() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        BASE_URL="http://127.0.0.1:8080",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="password",
        assert_local_base_url=lambda: None,
        wait_ready=lambda: None,
        login=lambda *args, **kwargs: {"id": 1},
        ensure_game=lambda *args, **kwargs: {"id": 99},
    )


def _make_state_payload() -> dict:
    return {
        "challenges": {
            "6": {
                "state": "accepted",
                "category": "crypto",
                "title": "cid-6",
                "submits": [
                    {"platform_response": "Accepted", "correct": True},
                ],
            },
            "7": {
                "state": "accepted",
                "category": "crypto",
                "title": "cid-7",
                "submits": [
                    {"platform_response": "Accepted", "correct": True},
                    {"platform_response": "Accepted", "correct": True},
                ],
            },
            "8": {
                "state": "accepted",
                "category": "web",
                "title": "cid-8",
                "submits": [],
            },
            "9": {
                "state": "accepted",
                "category": "web",
                "title": "cid-9",
                "submits": [],
            },
            "10": {
                "state": "accepted",
                "category": "reverse",
                "title": "cid-10",
                "submits": [
                    {"platform_response": "Accepted", "correct": True},
                ],
            },
            "11": {
                "state": "no_candidate",
                "category": "reverse",
                "title": "cid-11",
                "submits": [],
            },
        }
    }


class RunRealLLMSolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_root = self.root / "state" / "local-gzctf-real-llm"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root = self.root / "artifacts" / "challenges"
        self.artifacts_root.mkdir(parents=True, exist_ok=True)

        payload = _make_state_payload()
        (self.state_root / "submission_state.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.state_root / "sentinel.txt").write_text("keep-me", encoding="utf-8")
        for cid in sorted(run_real_llm_solve.TARGET_CIDS):
            cid_dir = self.artifacts_root / cid
            cid_dir.mkdir(parents=True, exist_ok=True)
            (cid_dir / "codex_candidates.json").write_text("[]", encoding="utf-8")

        self._patch_project = mock.patch.object(run_real_llm_solve, "PROJECT", self.root)
        self._patch_project.start()
        self._patch_load_env = mock.patch.object(run_real_llm_solve, "load_env", lambda: None)
        self._patch_load_env.start()
        self._patch_boot = mock.patch.object(run_real_llm_solve, "boot", _fake_boot())
        self._patch_boot.start()

    def tearDown(self):
        self._patch_boot.stop()
        self._patch_load_env.stop()
        self._patch_project.stop()
        self.tmp.cleanup()

    def _summary_path(self) -> Path:
        log_dir = self.root / "logs" / "local-gzctf-real-llm"
        candidates = sorted(log_dir.glob("real-llm-summary-*.json"))
        self.assertTrue(candidates, "expected a summary file")
        return candidates[-1]

    def _patch_supervisor_factory(self):
        def fake_build_supervisor(cfg: dict):
            state_path = self.root / cfg["submit"]["state_path"]
            if state_path.exists():
                snapshot = json.loads(state_path.read_text(encoding="utf-8"))
            else:
                snapshot = {"challenges": {}}
            return _FakeSupervisor(self.root, snapshot)

        return mock.patch.object(run_real_llm_solve, "build_supervisor", side_effect=fake_build_supervisor)

    def _patch_artifact_check(self):
        def fake_assert_artifacts_exist() -> dict:
            return {
                cid: {"dir": f"artifacts/challenges/{cid}", "present": [], "missing": []}
                for cid in sorted(run_real_llm_solve.TARGET_CIDS)
            }

        return mock.patch.object(
            run_real_llm_solve,
            "assert_artifacts_exist",
            side_effect=fake_assert_artifacts_exist,
        )

    def test_default_run_resets_state_root(self):
        with self._patch_supervisor_factory(), self._patch_artifact_check():
            rc = run_real_llm_solve.main([])

        self.assertEqual(rc, 0)
        self.assertFalse((self.state_root / "sentinel.txt").exists())

        summary = json.loads(self._summary_path().read_text(encoding="utf-8"))
        self.assertFalse(summary["resume_mode"])
        self.assertTrue(summary["state_reset_performed"])
        self.assertTrue(all(v == 0 for v in summary["submit_counts_before"].values()))
        self.assertTrue(all(v == 0 for v in summary["submit_counts_after"].values()))

    def test_no_reset_preserves_state_and_keeps_submit_counts(self):
        with self._patch_supervisor_factory(), self._patch_artifact_check():
            rc = run_real_llm_solve.main(["--no-reset"])

        self.assertEqual(rc, 0)
        self.assertTrue((self.state_root / "sentinel.txt").exists())

        summary = json.loads(self._summary_path().read_text(encoding="utf-8"))
        self.assertTrue(summary["resume_mode"])
        self.assertFalse(summary["state_reset_performed"])
        self.assertEqual(summary["submit_counts_before"], summary["submit_counts_after"])
        self.assertTrue(all(row["submit_count_delta"] == 0 for row in summary["rows"]))


if __name__ == "__main__":
    unittest.main()
