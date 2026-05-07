from __future__ import annotations
import argparse, json
from collections import defaultdict
from pathlib import Path
from ctf_agents.common.logging_jsonl import redact_text

def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def render(events: list[dict]) -> str:
    by_ch = defaultdict(list)
    for e in events: by_ch[e.get("challenge_id") or "global"].append(e)
    parts = ["# WriteUp 草稿", "", "> 由 agent 日志自动生成；提交前请人工核对、补充关键推理，并确认脱敏。", ""]
    for cid, evs in by_ch.items():
        if cid == "global": continue
        cat = next((e.get("category") for e in evs if e.get("category")), "unknown")
        parts += [f"## {cid}（{cat}）", "", "### 题目信息", "", "- 来源：比赛平台", "- 分类：" + str(cat), "", "### 解题过程", ""]
        for e in evs:
            et = e.get("event_type"); msg = redact_text(str(e.get("message", "")))
            if et in {"tool_call", "observation", "hypothesis", "flag_candidate", "submit_decision", "submit_result", "writeup_note"}: parts.append(f"- `{e.get('ts')}` **{et}** / {e.get('actor')}: {msg}")
        flags = [e for e in evs if e.get("event_type") == "flag_candidate"]; parts += ["", "### Flag 与验证", ""]
        if flags:
            for f in flags[-3:]:
                data = f.get("data") or {}; flag = redact_text(str(data.get("flag", "<候选见日志>"))); parts.append(f"- 候选：`{flag}`，置信度：{f.get('confidence', data.get('confidence', 'n/a'))}")
        else: parts.append("- 未记录 flag 候选。")
        parts += ["", "### 复现命令", "", "```bash", "# TODO: 人工补充最小复现命令", "```", ""]
    return "\n".join(parts)

def main() -> None:
    ap = argparse.ArgumentParser(description="JSONL 日志生成 WriteUp Markdown 草稿"); ap.add_argument("log", type=Path); ap.add_argument("--out", type=Path, default=None); args = ap.parse_args(); out = args.out or Path("writeups") / (args.log.stem + ".md"); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(render(load_events(args.log)), encoding="utf-8"); print(out)
if __name__ == "__main__": main()
