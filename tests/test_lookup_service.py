"""HTTP coverage for ``ctf_agents.knowledge.lookup_service``.

Uses FastAPI's TestClient to exercise both endpoints without binding a
port. Bank fixtures live entirely in tmpdir so the production
``question_bank_merged.json`` is untouched.

Each branch (judge / single / multi) gets at least one test; option
shuffling is verified to confirm the engine's mapping survives the
HTTP layer.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ctf_agents.knowledge.lookup_service import create_app


def _seed_bank(tmpdir: Path) -> Path:
    bank = {
        "questions": [
            {
                "qid": "judge-1",
                "type": "judge",
                "stem_raw": "国家支持创新网络安全管理方式，运用网络新技术。",
                "options_raw": [],
                "answer": ["T"],
                "flags": [],
            },
            {
                "qid": "judge-2",
                "type": "judge",
                "stem_raw": "网络产品必须符合所有国家标准的强制性要求。",
                "options_raw": [],
                "answer": ["F"],
                "flags": [],
            },
            {
                "qid": "single-1",
                "type": "single",
                "stem_raw": "下列哪项属于强密码特征？",
                "options_raw": [
                    {"key": "A", "text": "使用生日"},
                    {"key": "B", "text": "长度足够且包含多类字符"},
                    {"key": "C", "text": "全部使用小写字母"},
                    {"key": "D", "text": "使用单一字符"},
                ],
                "answer": ["B"],
                "flags": [],
            },
            {
                "qid": "multi-1",
                "type": "multi",
                "stem_raw": "信息通常包括在网络上传输的内容有：",
                "options_raw": [
                    {"key": "A", "text": "消息"},
                    {"key": "B", "text": "符号"},
                    {"key": "C", "text": "数据"},
                    {"key": "D", "text": "信号"},
                    {"key": "E", "text": "资料"},
                ],
                "answer": ["A", "B", "C", "D", "E"],
                "flags": [],
            },
            {
                "qid": "manual-review-1",
                "type": "single",
                "stem_raw": "源数据带有人工审计标记的题目示例。",
                "options_raw": [
                    {"key": "A", "text": "正确选项的文本"},
                    {"key": "B", "text": "错误选项的文本"},
                ],
                "answer": ["A"],
                "flags": ["manual_review_required:source_truncated_options"],
            },
        ]
    }
    p = tmpdir / "bank.json"
    p.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
    return p


class LookupServiceV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bank_path = _seed_bank(Path(cls._tmp.name))
        cls.client = TestClient(create_app(cls.bank_path))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_health_lists_endpoints(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["questions"], 5)
        self.assertIn("/lookup_v2", body["endpoints"])

    def test_lookup_v2_judge_true(self):
        resp = self.client.post(
            "/lookup_v2",
            json={"text": "国家支持创新网络安全管理方式，运用网络新技术。"},
        )
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertTrue(d["matched"])
        self.assertEqual(d["branch"], "judge")
        self.assertIn(d["answer_label"], ("正确", "对"))

    def test_lookup_v2_judge_false(self):
        resp = self.client.post(
            "/lookup_v2",
            json={"text": "网络产品必须符合所有国家标准的强制性要求。"},
        )
        d = resp.json()
        self.assertEqual(d["branch"], "judge")
        self.assertIn(d["answer_label"], ("错误", "错"))

    def test_lookup_v2_single_original_order(self):
        resp = self.client.post(
            "/lookup_v2",
            json={
                "text": "下列哪项属于强密码特征？",
                "options": [
                    "使用生日",
                    "长度足够且包含多类字符",
                    "全部使用小写字母",
                    "使用单一字符",
                ],
            },
        )
        d = resp.json()
        self.assertEqual(d["branch"], "single")
        self.assertEqual(d["answer_letters"], ["B"])

    def test_lookup_v2_single_shuffled_remaps(self):
        resp = self.client.post(
            "/lookup_v2",
            json={
                "text": "下列哪项属于强密码特征？",
                "options": [
                    "使用单一字符",
                    "使用生日",
                    "长度足够且包含多类字符",
                    "全部使用小写字母",
                ],
            },
        )
        d = resp.json()
        # Bank's correct option text "长度足够且包含多类字符" is now at index 2
        self.assertEqual(d["answer_letters"], ["C"])

    def test_lookup_v2_multi_all_correct(self):
        resp = self.client.post(
            "/lookup_v2",
            json={
                "text": "信息通常包括在网络上传输的内容有：",
                "options": ["消息", "符号", "数据", "信号", "资料"],
            },
        )
        d = resp.json()
        self.assertEqual(d["branch"], "multi")
        self.assertEqual(sorted(d["answer_letters"]), ["A", "B", "C", "D", "E"])

    def test_lookup_v2_multi_shuffled(self):
        resp = self.client.post(
            "/lookup_v2",
            json={
                "text": "信息通常包括在网络上传输的内容有：",
                "options": ["资料", "信号", "数据", "符号", "消息"],
            },
        )
        d = resp.json()
        self.assertEqual(d["branch"], "multi")
        self.assertEqual(sorted(d["answer_letters"]), ["A", "B", "C", "D", "E"])

    def test_lookup_v2_propagates_manual_review_flag(self):
        resp = self.client.post(
            "/lookup_v2",
            json={
                "text": "源数据带有人工审计标记的题目示例。",
                "options": ["正确选项的文本", "错误选项的文本"],
            },
        )
        d = resp.json()
        self.assertTrue(any(
            str(n).startswith("manual_review_required") for n in d["notes"]
        ))

    def test_lookup_v2_low_score_returns_unmatched(self):
        resp = self.client.post(
            "/lookup_v2",
            json={"text": "完全不存在的题干字符串与题库无关"},
        )
        d = resp.json()
        self.assertFalse(d["matched"])
        self.assertIn("low_stem_score", d["notes"])

    def test_legacy_lookup_endpoint_still_works(self):
        resp = self.client.post(
            "/lookup", json={"text": "下列哪项属于强密码特征？"}
        )
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertGreater(len(d["matches"]), 0)


if __name__ == "__main__":
    unittest.main()
