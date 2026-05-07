from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ctf_agents.knowledge.lookup_engine import LookupEngine


class LookupGuardTest(unittest.TestCase):
    def test_propagates_manual_review_flags_into_lookup_notes(self) -> None:
        bank = {
            "questions": [
                {
                    "qid": "q-manual",
                    "type": "single",
                    "stem_raw": "需要人工确认的题目",
                    "options_raw": [
                        {"key": "A", "text": "错误项"},
                        {"key": "B", "text": "正确项"},
                    ],
                    "answer": ["B"],
                    "flags": ["manual_review_required:truncated_option_text"],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as td:
            bank_path = Path(td) / "bank.json"
            bank_path.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
            engine = LookupEngine(bank_path)

            result = engine.lookup("需要人工确认的题目", ["错误项", "正确项"])

        self.assertEqual(result.answer_letters, ["B"])
        self.assertIn("manual_review_required:truncated_option_text", result.notes)

    def test_propagates_source_answer_out_of_options_after_repair(self) -> None:
        bank = {
            "questions": [
                {
                    "qid": "q-source-mismatch",
                    "type": "multi",
                    "stem_raw": "电子数据完整性保护方法",
                    "options_raw": [
                        {"key": "A", "text": "扣押、封存电子数据原始存储介质"},
                        {"key": "B", "text": "计算电子数据完整性校验值"},
                        {"key": "C", "text": "封存电子数据备份"},
                        {"key": "D", "text": "冻结电子数据"},
                        {"key": "E", "text": "制作电子数据备份"},
                        {"key": "F", "text": "对收集、提取电子数据的相关活动进行录像"},
                    ],
                    "answer": ["A", "B", "C", "D", "F"],
                    "flags": ["manual_review_required:source_answer_out_of_options"],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as td:
            bank_path = Path(td) / "bank.json"
            bank_path.write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
            engine = LookupEngine(bank_path)

            options = [o["text"] for o in bank["questions"][0]["options_raw"]]
            result = engine.lookup("电子数据完整性保护方法", options)

        self.assertEqual(result.answer_letters, ["A", "B", "C", "D", "F"])
        self.assertIn("manual_review_required:source_answer_out_of_options", result.notes)


if __name__ == "__main__":
    unittest.main()
