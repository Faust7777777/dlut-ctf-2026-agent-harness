from __future__ import annotations

import unittest

from scripts.option_anomaly_scan import analyze_question


class OptionAnomalyScanTest(unittest.TestCase):
    def test_flags_answer_letter_outside_parsed_options(self) -> None:
        q = {
            "qid": "q1",
            "type": "multi",
            "answer": ["A", "C"],
            "options_raw": [{"key": "A", "text": "one"}, {"key": "B", "text": "two"}],
        }

        issues = analyze_question(q)

        self.assertIn("answer_letter_outside_options", issues)

    def test_flags_known_suspicious_fragment(self) -> None:
        q = {
            "qid": "q2",
            "type": "single",
            "answer": ["A"],
            "options_raw": [{"key": "A", "text": "非否"}, {"key": "B", "text": "正常选项"}],
        }

        issues = analyze_question(q)

        self.assertIn("suspicious_fragment", issues)

    def test_does_not_flag_normal_numeric_single_choice(self) -> None:
        q = {
            "qid": "q3",
            "type": "single",
            "answer": ["B"],
            "options_raw": [
                {"key": "A", "text": "15"},
                {"key": "B", "text": "20"},
                {"key": "C", "text": "30"},
                {"key": "D", "text": "45"},
            ],
        }

        issues = analyze_question(q)

        self.assertNotIn("possible_truncated_option", issues)

    def test_does_not_flag_source_three_option_single_choice(self) -> None:
        q = {
            "qid": "2020-practice-0054",
            "type": "single",
            "answer": ["A"],
            "options_raw": [
                {"key": "A", "text": "国家职能监管机构"},
                {"key": "B", "text": "外部合作组织"},
                {"key": "C", "text": "专家顾问组"},
            ],
        }

        issues = analyze_question(q)

        self.assertNotIn("few_options", issues)

    def test_does_not_flag_fixed_storage_backup_option(self) -> None:
        q = {
            "qid": "2020-practice-0375",
            "type": "multi",
            "answer": ["A", "B", "C", "D", "E"],
            "options_raw": [
                {"key": "A", "text": "备份网络"},
                {"key": "B", "text": "备份个人计算机"},
                {"key": "C", "text": "专门的存储备份网络"},
                {"key": "D", "text": "备份本地计算机"},
                {"key": "E", "text": "备份服务器"},
            ],
        }

        issues = analyze_question(q)

        self.assertNotIn("suspicious_fragment", issues)

    def test_does_not_flag_completed_user_group_option(self) -> None:
        q = {
            "qid": "2020-tech-0312",
            "type": "single",
            "answer": ["A"],
            "options_raw": [
                {"key": "A", "text": "大规模网络和大规模用户群"},
                {"key": "B", "text": "小规模网络和小规模用户群"},
                {"key": "C", "text": "大规模网络和小规模用户群"},
                {"key": "D", "text": "小规模网络和大规模用户群"},
            ],
        }

        issues = analyze_question(q)

        self.assertNotIn("possible_truncated_option", issues)

    def test_does_not_flag_restored_compliance_0752_option_f(self) -> None:
        q = {
            "qid": "2020-compliance-0752",
            "type": "multi",
            "answer": ["A", "B", "C", "D", "F"],
            "options_raw": [
                {"key": "A", "text": "扣押、封存电子数据原始存储介质"},
                {"key": "B", "text": "计算电子数据完整性校验值"},
                {"key": "C", "text": "封存电子数据备份"},
                {"key": "D", "text": "冻结电子数据"},
                {"key": "E", "text": "制作电子数据备份"},
                {"key": "F", "text": "对收集、提取电子数据的相关活动进行录像"},
            ],
        }

        issues = analyze_question(q)

        self.assertNotIn("answer_letter_outside_options", issues)
        self.assertNotIn("too_many_options", issues)


if __name__ == "__main__":
    unittest.main()
