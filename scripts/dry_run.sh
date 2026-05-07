#!/usr/bin/env bash
set -euo pipefail
mkdir -p data/raw data/processed logs writeups
if [ ! -f configs/config.yaml ]; then cp configs/config.example.yaml configs/config.yaml; fi
cat > data/raw/sample_bank.txt <<'DRYTXT'
1. 下列哪项属于强密码特征？
A. 使用生日
B. 长度足够且包含多类字符
C. 与用户名相同
D. 只包含数字
答案：B

2. 判断题：HTTPS 一定能防止服务端被入侵。
答案：错误

3. 多选题：常见的 Web 安全风险包括哪些？
A. SQL 注入
B. XSS
C. CSRF
D. 正常登录
答案：ABC
DRYTXT
python -m ctf_agents.knowledge.build_bank data/raw/sample_bank.txt --out data/processed/question_bank.json
python -m ctf_agents.knowledge.lookup_cli "HTTPS 能防止服务端入侵吗" --bank data/processed/question_bank.json
python -m ctf_agents.submit.flag_guard 'flag{dry_run_example}' --challenge-id dryrun-misc --category misc --evidence-count 3 --extraction-confidence 0.95
cat > /tmp/challenge.json <<'DRYJSON'
{"id":"web-1","title":"easy jwt cookie","category":"web","description":"login and cookie issue","attachments":[],"url":""}
DRYJSON
python -m ctf_agents.skill.router /tmp/challenge.json
python - <<'DRYPY'
from ctf_agents.common.logging_jsonl import JsonlLogger
lg=JsonlLogger('logs','dryrun')
lg.event('challenge_seen','coordinator','看到 dry-run 题目',challenge_id='dryrun-misc',category='misc')
lg.event('hypothesis','codex','样例题直接从本地题库 lookup',challenge_id='dryrun-misc',category='misc',confidence=0.9)
lg.event('flag_candidate','codex','提取到候选 flag',challenge_id='dryrun-misc',category='misc',data={'flag':'flag{dry_run_example}'},confidence=0.95)
lg.event('submit_decision','guard','进入人工确认，不自动提交',challenge_id='dryrun-misc',category='misc',confidence=0.95)
print(lg.path)
DRYPY
python -m ctf_agents.writeup.generate_writeup logs/dryrun.jsonl --out writeups/dryrun.md
printf '[OK] dry-run finished. Check writeups/dryrun.md\n'
