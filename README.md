# DLUT CTF Multi-Agent Skeleton

用途：在授权的校内网络安全知识与技能竞赛环境内，提供知识赛题库检索、技能赛任务分流、flag 候选审核、飞书通知、JSONL 日志与 WriteUp 草稿生成的最小可运行骨架。

默认安全策略：
- 不扫描、访问、攻击非比赛授权目标。
- 不把题库 PDF 全量发送到云端 LLM；PDF 解析默认本地完成。
- flag 自动提交默认关闭；开启前必须配置平台适配器、题目白名单、频率限制与人审策略。

## 快速启动

```bash
cd dlut_ctf_agent_skeleton
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp configs/config.example.yaml configs/config.yaml
python -m ctf_agents.knowledge.lookup_cli --help
```

## 最小 dry-run

```bash
bash scripts/dry_run.sh
```

## 目录

- `ctf_agents/knowledge`: PDF 抽取、题库 JSON 构建、lookup CLI/API。
- `ctf_agents/skill`: 技能赛题目路由。
- `ctf_agents/submit`: flag 格式、置信度、频率限制、平台提交适配。
- `ctf_agents/common`: 配置、日志、飞书、scope guard。
- `ctf_agents/writeup`: 日志转 Markdown WriteUp 草稿。
- `prompts`: Codex CLI / Claude Code 可直接读取的 agent 提示词模板。
- `schemas`: 题库与日志 JSON schema。
- `runbooks`: 演练步骤。
