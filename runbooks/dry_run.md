# Dry-run Runbook

## 0. 前置

```bash
cd dlut_ctf_agent_skeleton
bash scripts/setup_wsl.sh
cp .env.example .env
cp configs/config.example.yaml configs/config.yaml
```

## 1. 知识赛样例

```bash
bash scripts/dry_run.sh
```

预期：
- `data/processed/question_bank.json` 存在。
- lookup 能返回 HTTPS 判断题答案“错误”。
- `writeups/dryrun.md` 生成。

## 2. 题库 PDF 真实流程

```bash
cp /path/to/question_bank.pdf data/raw/question_bank.pdf
python -m ctf_agents.knowledge.pdf_extract data/raw/question_bank.pdf --out data/processed/pdf_extract
python -m ctf_agents.knowledge.build_bank data/processed/pdf_extract/all.txt --pdf data/raw/question_bank.pdf --out data/processed/question_bank.json
python -m ctf_agents.knowledge.lookup_cli "复制一道题干到这里" --bank data/processed/question_bank.json
```

验收：随机抽 30 题，题干命中率和答案正确率均 ≥ 95%；若低于阈值，人工整理 PDF 文本或改正则。

## 3. flag guard 演练

```bash
python -m ctf_agents.submit.flag_guard 'flag{example_123}' --challenge-id test1 --category misc --evidence-count 3 --extraction-confidence 0.95
python -m ctf_agents.submit.flag_guard 'flag{maybe}' --challenge-id pwn1 --category pwn --evidence-count 1 --extraction-confidence 0.6
```

预期：高置信 misc 在 auto_submit=false 时不直接提交；pwn 进入人审或 hold。

## 4. WriteUp 演练

```bash
python -m ctf_agents.writeup.generate_writeup logs/dryrun.jsonl --out writeups/dryrun.md
```

预期：Markdown 可读，无 API key/cookie/个人路径。
