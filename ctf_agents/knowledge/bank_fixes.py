from __future__ import annotations

import copy
from typing import Any


OPTION_TEXT_FIXES: dict[str, dict[str, str]] = {
    "2020-tech-0312": {
        "A": "大规模网络和大规模用户群",
        "B": "小规模网络和小规模用户群",
        "C": "大规模网络和小规模用户群",
        "D": "小规模网络和大规模用户群",
    },
    "2020-tech-0313": {
        "A": "安全电子邮件",
        "B": "匿名登陆",
        "C": "安全Web服务",
        "D": "VPN应用",
        "E": "网上商业或政务行为",
    },
}

OPTION_LIST_FIXES: dict[str, list[dict[str, str]]] = {
    "2020-compliance-0752": [
        {"key": "A", "text": "扣押、封存电子数据原始存储介质"},
        {"key": "B", "text": "计算电子数据完整性校验值"},
        {"key": "C", "text": "封存电子数据备份"},
        {"key": "D", "text": "冻结电子数据"},
        {"key": "E", "text": "制作电子数据备份"},
        {"key": "F", "text": "对收集、提取电子数据的相关活动进行录像"},
    ],
    "2020-practice-0016": [
        {"key": "A", "text": "把信息系统安全从技术扩展到管理"},
        {"key": "B", "text": "把信息系统安全从静态扩展到动态"},
        {"key": "C", "text": "通过各种安全保障技术和安全保障管理措施的综合融合至信息化"},
        {"key": "D", "text": "形成对信息、信息系统、业务的保障"},
        {"key": "E", "text": "防止信息泄露，保障业务安全。"},
    ],
    "2020-practice-0279": [
        {"key": "A", "text": "红色预警"},
        {"key": "B", "text": "橙色预警"},
        {"key": "C", "text": "黄色预警"},
        {"key": "D", "text": "蓝色预警"},
    ],
    "2020-practice-0283": [
        {"key": "A", "text": "可能对特别重要的网络安全保护对象产生较大或一般的损害"},
        {"key": "B", "text": "可能对重要的网络安全保护对象产生严重或较大的损害"},
        {"key": "C", "text": "可能对一般的网络安全保护对象产生特别严重或严重的损害"},
        {"key": "D", "text": "可能对重要的网络安全保护对象产生一般的损害"},
        {"key": "E", "text": "可能对一般的网络安全保护对象产生较大或一般的损害"},
    ],
    "2020-practice-0286": [
        {"key": "A", "text": "标识信息系统的资产价值"},
        {"key": "B", "text": "识别信息系统面临的自然和人为的威胁"},
        {"key": "C", "text": "识别信息系统的脆弱性"},
        {"key": "D", "text": "分析各种威胁发生的可能性"},
        {"key": "E", "text": "网络安全预警的判定"},
    ],
    "2020-practice-0375": [
        {"key": "A", "text": "备份网络"},
        {"key": "B", "text": "备份个人计算机"},
        {"key": "C", "text": "专门的存储备份网络"},
        {"key": "D", "text": "备份本地计算机"},
        {"key": "E", "text": "备份服务器"},
    ],
}

STEM_TEXT_FIXES: dict[str, str] = {
    "2020-practice-0279": "当发生极其严重的网络安全事件或威胁，可能极大威胁国家安全、引起社会动荡、对经济建设有极其恶劣的负面影响，或严重损害公众利益，应发布（）",
    "2020-practice-0283": "当发生一般的网络安全事件或威胁，应发布蓝色预警，特别轻微的可以不发布预警，包括以下情况：（）",
    "2020-practice-0286": "风险评估包括（）",
}

DERIVED_FLAGS_TO_RECHECK = {
    "answer_out_of_options",
    "single_too_few_options",
    "multi_too_few_options",
    "too_many_options",
}

MANUAL_REVIEW_FLAGS: dict[str, str] = {
    "2020-compliance-0752": "manual_review_required:source_answer_out_of_options",
    "2020-tech-0040": "manual_review_required:truncated_option_text",
    "2020-compliance-0248": "manual_review_required:truncated_option_text",
}


def _add_flag(q: dict[str, Any], flag: str) -> None:
    flags = q.setdefault("flags", [])
    if flag not in flags:
        flags.append(flag)


def _replace_option_texts(q: dict[str, Any], fixes: dict[str, str]) -> None:
    for opt in q.get("options_raw", []):
        key = opt.get("key")
        if key in fixes:
            opt["text"] = fixes[key]
    _add_flag(q, "manual_fix:option_text")


def _replace_option_list(q: dict[str, Any], options: list[dict[str, str]]) -> None:
    q["options_raw"] = copy.deepcopy(options)
    q["flags"] = [flag for flag in q.get("flags", []) if flag not in DERIVED_FLAGS_TO_RECHECK]
    _add_flag(q, "manual_fix:option_list")


def _replace_stem_text(q: dict[str, Any], stem: str) -> None:
    q["stem_raw"] = stem
    _add_flag(q, "manual_fix:stem_text")


def apply_known_fixes(question: dict[str, Any]) -> dict[str, Any]:
    q = copy.deepcopy(question)
    qid = q.get("qid", "")

    if qid in OPTION_TEXT_FIXES:
        _replace_option_texts(q, OPTION_TEXT_FIXES[qid])

    if qid in OPTION_LIST_FIXES:
        _replace_option_list(q, OPTION_LIST_FIXES[qid])

    if qid in STEM_TEXT_FIXES:
        _replace_stem_text(q, STEM_TEXT_FIXES[qid])

    if qid in MANUAL_REVIEW_FLAGS:
        _add_flag(q, MANUAL_REVIEW_FLAGS[qid])

    return q


def apply_known_fixes_to_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [apply_known_fixes(q) for q in questions]
