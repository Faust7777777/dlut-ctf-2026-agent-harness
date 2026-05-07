from __future__ import annotations
from dataclasses import dataclass
import argparse, json

@dataclass
class Challenge:
    id: str; title: str; category: str = ""; description: str = ""; attachments: list[str] | None = None; url: str = ""

ROUTE_RULES = [("pwn", ["pwn", "rop", "heap", "libc", "canary", "ret2", "format string", "栈", "堆"]), ("reverse", ["reverse", "re", "apk", "elf", "exe", "ida", "ghidra", "逆向", "反编译"]), ("web", ["web", "http", "sql", "xss", "ssti", "ssrf", "upload", "cookie", "jwt", "php", "flask"]), ("forensics", ["forensic", "取证", "memory", "dump", "pcap", "volatility", "wireshark", "disk", "流量"]), ("misc", ["misc", "crypto", "stego", "zip", "png", "jpg", "audio", "base64", "二维码", "隐写", "杂项", "密码"])]

def route(ch: Challenge) -> dict:
    hay = " ".join([ch.title, ch.category, ch.description, " ".join(ch.attachments or [])]).lower(); scores = {}
    for cat, kws in ROUTE_RULES: scores[cat] = sum(1 for k in kws if k.lower() in hay)
    if ch.category.lower() in scores: scores[ch.category.lower()] += 3
    best = max(scores, key=scores.get)
    if scores[best] == 0: best = "misc"
    return {"route": best, "scores": scores, "review_required": best in {"pwn", "reverse"}}

def main() -> None:
    ap = argparse.ArgumentParser(description="技能赛题目分流"); ap.add_argument("json_file"); args = ap.parse_args(); obj = json.load(open(args.json_file, encoding="utf-8")); print(json.dumps(route(Challenge(**obj)), ensure_ascii=False, indent=2))
if __name__ == "__main__": main()
