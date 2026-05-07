from __future__ import annotations

import unittest

from ctf_agents.knowledge.bank_fixes import apply_known_fixes


class BankFixesTest(unittest.TestCase):
    def test_fixes_pki_user_group_spillover(self) -> None:
        q = {
            "qid": "2020-tech-0312",
            "type": "single",
            "options_raw": [
                {"key": "A", "text": "大规模网络和大规模用"},
                {"key": "B", "text": "小规模网络和小规模用"},
                {"key": "C", "text": "大规模网络和小规模用"},
                {"key": "D", "text": "小规模网络和大规模用"},
            ],
            "flags": [],
        }

        out = apply_known_fixes(q)

        self.assertEqual(out["options_raw"][0]["text"], "大规模网络和大规模用户群")
        self.assertEqual(out["options_raw"][1]["text"], "小规模网络和小规模用户群")
        self.assertIn("manual_fix:option_text", out["flags"])

    def test_marks_unresolved_source_issue_without_rewriting_answer(self) -> None:
        q = {
            "qid": "2020-compliance-0752",
            "type": "multi",
            "answer": ["A", "B", "C", "D", "F"],
            "options_raw": [{"key": "A", "text": "扣押"}],
            "flags": ["answer_out_of_options"],
        }

        out = apply_known_fixes(q)

        self.assertEqual(out["answer"], ["A", "B", "C", "D", "F"])
        self.assertIn("manual_review_required:source_answer_out_of_options", out["flags"])

    def test_restores_compliance_0752_missing_option_f_from_explanation(self) -> None:
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
            ],
            "flags": ["answer_out_of_options"],
        }

        out = apply_known_fixes(q)

        self.assertEqual([o["key"] for o in out["options_raw"]], ["A", "B", "C", "D", "E", "F"])
        self.assertEqual(out["options_raw"][5]["text"], "对收集、提取电子数据的相关活动进行录像")
        self.assertEqual(out["answer"], ["A", "B", "C", "D", "F"])
        self.assertNotIn("answer_out_of_options", out["flags"])
        self.assertIn("manual_review_required:source_answer_out_of_options", out["flags"])

    def test_restores_missing_practice_0016_option_b(self) -> None:
        q = {
            "qid": "2020-practice-0016",
            "type": "multi",
            "answer": ["A", "B", "C", "D"],
            "options_raw": [
                {"key": "A", "text": "把信息系统安全从技术扩展到管理"},
                {"key": "C", "text": "通过各种安全保障技术和安全保障管理措施的综合融合至信息化"},
                {"key": "D", "text": "形成对信息、信息系统、业务的保障"},
                {"key": "E", "text": "防止信息泄露，保障业务安全。"},
            ],
            "flags": [],
        }

        out = apply_known_fixes(q)

        self.assertEqual([o["key"] for o in out["options_raw"]], ["A", "B", "C", "D", "E"])
        self.assertEqual(out["options_raw"][1]["text"], "把信息系统安全从静态扩展到动态")
        self.assertEqual(out["answer"], ["A", "B", "C", "D"])

    def test_restores_practice_0279_red_warning_as_option_a(self) -> None:
        q = {
            "qid": "2020-practice-0279",
            "type": "single",
            "answer": ["A"],
            "stem_raw": "当发生极其严重的网络安全事件或威胁，可能极大威胁国家安全、引起社会动荡、对经济红色预警建设有极其恶劣的负面影响，或严重损害公众利益，应发布（）",
            "options_raw": [
                {"key": "B", "text": "橙色预警"},
                {"key": "C", "text": "黄色预警"},
                {"key": "D", "text": "蓝色预警"},
            ],
            "flags": [],
        }

        out = apply_known_fixes(q)

        self.assertEqual([o["key"] for o in out["options_raw"]], ["A", "B", "C", "D"])
        self.assertEqual(out["options_raw"][0]["text"], "红色预警")
        self.assertIn("经济建设", out["stem_raw"])
        self.assertNotIn("经济红色预警建设", out["stem_raw"])
        self.assertEqual(out["answer"], ["A"])

    def test_restores_practice_0283_leaked_option_a(self) -> None:
        q = {
            "qid": "2020-practice-0283",
            "type": "multi",
            "answer": ["C", "D"],
            "stem_raw": "可能对特别重要的当发生一般的网络安全事件或网络安全威胁，应发布蓝色预警，特别保护对象轻微的可以不发布预警，包括产生较大以下情况：（）或一般的损害",
            "options_raw": [
                {"key": "B", "text": "可能对重要的网络安全保护对象产生严重或较大的损害"},
                {"key": "C", "text": "可能对一般的网络安全保护对象产生特别严重或严重的损害"},
                {"key": "D", "text": "可能对重要的网络安全保护对象产生一般的损害"},
                {"key": "E", "text": "可能对一般的网络安全保护对象产生较大或一般的损害"},
            ],
            "flags": [],
        }

        out = apply_known_fixes(q)

        self.assertEqual([o["key"] for o in out["options_raw"]], ["A", "B", "C", "D", "E"])
        self.assertEqual(out["stem_raw"], "当发生一般的网络安全事件或威胁，应发布蓝色预警，特别轻微的可以不发布预警，包括以下情况：（）")
        self.assertEqual(out["options_raw"][0]["text"], "可能对特别重要的网络安全保护对象产生较大或一般的损害")
        self.assertEqual(out["answer"], ["C", "D"])

    def test_restores_practice_0286_leaked_option_a(self) -> None:
        q = {
            "qid": "2020-practice-0286",
            "type": "multi",
            "answer": ["A", "B", "C", "D"],
            "stem_raw": "标识信息风险评估包括（） 系统的资产价值",
            "options_raw": [
                {"key": "B", "text": "识别信息系统面临的自然和人为的威胁"},
                {"key": "C", "text": "识别信息系统的脆弱性"},
                {"key": "D", "text": "分析各种威胁发生的可能性"},
                {"key": "E", "text": "网络安全预警的判定"},
            ],
            "flags": [],
        }

        out = apply_known_fixes(q)

        self.assertEqual(out["stem_raw"], "风险评估包括（）")
        self.assertEqual([o["key"] for o in out["options_raw"]], ["A", "B", "C", "D", "E"])
        self.assertEqual(out["options_raw"][0]["text"], "标识信息系统的资产价值")
        self.assertEqual(out["answer"], ["A", "B", "C", "D"])

    def test_restores_practice_0375_merged_backup_option(self) -> None:
        q = {
            "qid": "2020-practice-0375",
            "type": "multi",
            "answer": ["A", "B", "C", "D", "E"],
            "options_raw": [
                {"key": "A", "text": "备份网络"},
                {"key": "B", "text": "备份个人计算机"},
                {"key": "C", "text": "专门的存备份本地储备份网计算机络"},
                {"key": "E", "text": "备份服务器"},
            ],
            "flags": ["answer_out_of_options"],
        }

        out = apply_known_fixes(q)

        self.assertEqual([o["key"] for o in out["options_raw"]], ["A", "B", "C", "D", "E"])
        self.assertEqual(out["options_raw"][2]["text"], "专门的存储备份网络")
        self.assertEqual(out["options_raw"][3]["text"], "备份本地计算机")
        self.assertEqual(out["answer"], ["A", "B", "C", "D", "E"])
        self.assertNotIn("answer_out_of_options", out["flags"])


if __name__ == "__main__":
    unittest.main()
