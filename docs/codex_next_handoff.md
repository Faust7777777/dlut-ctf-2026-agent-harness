# Codex Next Handoff — 2026-05-07

> 本文给 Codex 接续主开发。当前阶段：代码侧已冻结，进入文档/runbook 编写。
> 上一份 handoff（Codex → Opus）：`docs/opus_next_handoff.md`。

## 当前结论

代码侧 P0 / P1 全部闭环，进入**文档**和**5/9 彩排准备**阶段。

**接下来你（Codex）要做的事**：

- **P0**：写 `runbooks/contest_day.md` 比赛日 runbook（详情见下文 §"P0: 写 contest_day.md"）
- **P1（可选）**：写 `docs/organizer_responses.md` 留痕（用户已表示"不用了"，但你认为有必要可以独立判断）
- **P2（等组委会回复）**：用户已发"技能赛是 CTFd / GZCTF / 自研，有测试入口吗？"——拿到回复后补技能赛平台 adapter

**不要做的事**：

- 不要扩功能、不要重写已通过测试的状态机 / lookup engine / wjx assist
- 不要改 `configs/config.yaml` 里的口径默认值（`cloud_llm_allowed: true` 等是用户拍板的）
- 不要在文档/代码里发真实飞书消息（preview 模式即可，除非 `--send-feishu` 显式触发）
- 不要扩展 A2 真实 CTF 题或 lookup_cli 剪贴板 UX

## Opus 这一轮做了什么

按 Codex 之前那份 handoff 的 P0 优先级推进：

### P0a `scripts/rehearsal_5_9.py` — 一键彩排

7 场景：mock workflow / kill switch / freeze / force_submit / rate limit / 飞书 preview / 问卷星 dry-run（可选）。

跑：

```bash
python scripts/rehearsal_5_9.py            # 全部 7 场景
python scripts/rehearsal_5_9.py --scenario kill_switch --scenario freeze
python scripts/rehearsal_5_9.py --send-feishu  # 真发一条 kill_switch 测试到 .env 里的 webhook
python scripts/rehearsal_5_9.py --wjx-url '...' --wjx-password-env WJX_TEST_PASSWORD
```

输出：JSON summary 到 stdout + JSONL 到 `logs/rehearsal-<ts>.jsonl`。

### P0b `scripts/wjx_exam_assist.js` — 工程化问卷星 assist

基于 MVP（`scripts/wjx_exam_mvp.js`）改造而来，**保留所有 MVP 行为**。新增能力：

| 能力 | 实现位置 |
|---|---|
| HTTP lookup 替换静态答案 | `--lookup-url`，默认 `http://127.0.0.1:8765/lookup_v2`；`--answers` 仍作 fallback |
| 自动点击门 | `stem_score >= --score-threshold`（默认 92）+ 无 `manual_review_required:*` + 无 `negation_mismatch` / `close_second_candidate` 等风险 notes + 多选必须全映射成功 |
| 风险题高亮 | 不通过自动点击门时，`highlightOption()` 给选项加橙色 outline + 浅黄背景，操作员目视确认 |
| 短信验证码人工接管 | `--wait-human-auth` 检测到 SMS 关键词后暂停，最多 600s 等题块出现 |
| 密码用过检测 | 识别 `This password has been used.` / `密码已被使用`，明确退出 code 3 |
| 敏感字段脱敏 | JSONL 永远不记 password / 验证码 / 手机号；身份字段只记 `value_set: true` |

配套：

- `ctf_agents/knowledge/lookup_service.py` 加 `POST /lookup_v2` 端点，使用现代 `LookupEngine` 三分支；老 `POST /lookup` 端点保留兼容。
- `package.json` + `package-lock.json` 项目级固定 `playwright: 1.59.1`。
- `setup_wsl.sh` 加 `npm install` + `npx playwright install chromium`。

### Codex review 4 项闭环

| Codex 项 | 修法 |
|---|---|
| **High-1** 判断题 `正确↔对` 映射缺失 | 加 `JUDGE_TRUE_LABELS`/`JUDGE_FALSE_LABELS` 同义词表 + `judgeOptionPolarity()` 按极性分组 |
| **High-2** auto-click 没拦 `negation_mismatch` | 加 `RISKY_NOTES_EXACT` + `RISKY_NOTES_PREFIX` + `isRiskyNote()`；refactor `buildClickPlanFromLookup` 把 indexes 计算放在 risky-note 门**之前**，indexes 仍能给 highlighter 用 |
| **Medium-3** rehearsal 跑旧 MVP | rehearsal `scenario_wjx_dryrun` 改优先 `wjx_exam_assist.js` + `--lookup-url` 透传 |
| **Medium-4** /lookup_v2 + assist 没单测 | 装 httpx；新增 `tests/test_lookup_service.py` (10 测) + `tests/test_wjx_assist_logic.js` (19 断言组) |

### Codex 第二轮 review 3 项闭环

| 项 | 修法 |
|---|---|
| **httpx 缺 requirements 声明** | `requirements.txt` 加 `httpx>=0.28.0`；preflight Python 检查段加 `httpx` + `fastapi` |
| **playwright 寄生父目录** | 项目根新增 `package.json` + `package-lock.json`，`require.resolve('playwright')` 现解析到项目内 |
| **preflight 没检查 node/playwright** | preflight 加 "Node / Playwright" 段，5 项 PASS |
| **`.gitignore` 缺 node_modules/** | `.gitignore` 加 `node_modules/`、`*.log`、`.cache/`、`.venv/`、`.coverage`、`*.egg-info/` |

## 当前测试矩阵

| 套 | 数量 |
|---|---|
| `tests/test_lookup_guard.py` | 2 |
| `tests/test_bank_fixes.py` | 7 |
| `tests/test_option_anomaly.py` | 8 |
| `tests/test_flag_guard.py` | 23（含 race / 跨进程 / 远古时间戳 / 时钟回跳） |
| `tests/test_notifications.py` | 6 |
| `tests/test_skill_workflow.py` | 9 |
| `tests/test_lookup_service.py`（新） | 10 |
| `tests/test_wjx_assist_logic.js`（新） | 19 |
| **unit 总数** | **84** |
| `scripts/smoke_test_lookup.py` | 13/13 |
| `scripts/option_anomaly_scan.py` | 1 row remaining (known) |
| `scripts/skill_workflow_dryrun.py` | 7 paths |
| `scripts/skill_workflow_realctf.py` | 4 BJDCTF Misc real challenges |
| `scripts/rehearsal_5_9.py` | 7 scenarios (1 SKIP if no --wjx-url) |
| `scripts/preflight.sh` | 29 PASS / 0 WARN / 0 FAIL |

`bash scripts/run_all_tests.sh` → **ALL CHECKS PASSED**。

## 用户口径（已确认）

组委会答复（口语转述但用户视为官方答复）：

| 问题 | 答复 | 代码影响 |
|---|---|---|
| 云 API 算"本地大模型"吗 | ✅ 算 | `compliance.cloud_llm_allowed: true` 已对齐 |
| 题库 PDF 发云 LLM 算泄题吗 | ✅ 不算 | `compliance.knowledge_pdf_to_cloud_allowed: true` 已对齐 |
| flag 提交频率限制 | "按正常规则" | flag_guard 默认（错 2 冻结 / 全局 25s / 单题 90s）保留 |
| 知识赛平台 | **问卷星**（学校平台），事先收集学号+姓名 | `wjx_exam_assist.js` 主路径 |
| 切换标签/窗口 | ✅ 允许 | lookup_cli 旁开浏览器无问题 |
| 题目顺序 / 选项是否乱序 | "不知道" | 按乱序准备（三分支映射已有） |
| 是否允许返回修改 | "不能说" | **按"不能返回"准备**，赛中策略：低信题不要跳 |
| 是否需短信验证码 | 未明 | `--wait-human-auth` 已就绪 |
| 是否需问卷密码 | 未明 | `--password-env` 已就绪 |
| 浏览器自动化是否允许 | 未明 | 默认 headed 模式，只点击不绕过 |
| 技能赛平台 | **5/7 已发问，未回复** | 平台 adapter 仍延迟 |
| WriteUp 截止 | 5/11 前 | 与原 24h（5/11 17:30）一致 |

用户**未走**完全的留痕流程（"已确认合规"为口头判定），未保存规则截图到 `data/raw/rule_screenshots/`，未生成 `docs/organizer_responses.md`。

## P0：写 `runbooks/contest_day.md`

用户希望你写比赛日 runbook。建议结构（Opus 与用户讨论时给的版本）：

```
## T-60 (12:00) 冷启动检查清单
- 启动 WSL、激活 venv、source tools/env.sh
- bash scripts/preflight.sh 期望 pass=29
- 启动 lookup service (后台)：
    python -m ctf_agents.knowledge.lookup_service \
      --bank data/processed/question_bank_merged.json --port 8765 &
- curl http://127.0.0.1:8765/health 期望 questions=2815
- 检查 .env 是否齐全 (FEISHU_WEBHOOK / FEISHU_SECRET / API keys)
- 检查 .auto_submit_off 文件不存在
- 飞书测试：python scripts/rehearsal_5_9.py --scenario feishu --send-feishu
- 桌面布局：左浏览器 / 中两个终端 (lookup + assist) / 右飞书

## 13:00–14:00 知识赛 (问卷星)
- 等组委会发出问卷链接
- 命令模板（按收集到的鉴权方式分支）：
  ### 分支 A：只有问卷密码
  node scripts/wjx_exam_assist.js \
    --url '<问卷链接>' \
    --password-env WJX_EXAM_PASSWORD \
    --identity '{"姓名":"YOUR_NAME","工号":"YOUR_ID"}' \
    --lookup-url http://127.0.0.1:8765/lookup_v2 \
    --no-submit \
    --log "logs/wjx-knowledge-$(date +%Y%m%d-%H%M%S).jsonl"
  ### 分支 B：有短信验证码
  在分支 A 基础上加 --wait-human-auth
  ### 分支 C：什么鉴权都没
  分支 A 去掉 --password-env
- 操作员看到 highlight（橙色虚线）的题手动点击；其它已 auto-click
- 最后人工 review 一遍 → 确认无误 → 在浏览器手动点提交

## 14:00–17:00 技能赛 (CTF)
- 等组委会公布平台地址 / 题面
- (若公布平台是 CTFd) ... (若是 GZCTF) ... (若是自研) ...
- router 分流：
  python -m ctf_agents.skill.router <ch>.json
- 每题独立 workspace：mkdir workspace/<ch_id>; cd workspace/<ch_id>
- agent + flag_guard 决策树（详见图）
- flag 提交策略：
    Misc / 取证 高置信 → auto_submit (经 guard 限频)
    Web → human_review (默认)
    Pwn / Reverse → human_review (强制)
- 单题 8 分钟无进展 → 飞书通知；25 分钟无路径 → 换题
- kill switch 操作：
    touch .auto_submit_off    # 立即关闭自动提交
    rm   .auto_submit_off     # 解除
- 紧急人工提交：
    python -m ctf_agents.submit.force_submit \
      --challenge-id <id> --flag '<flag>' --category <cat> \
      --reason '<≥10字理由>' --commit

## 17:00–17:30 收尾
- 停止开新高难题
- 整理已解题 workspace；补 writeup_note
- 确认所有 flag 提交记录
- 不再点新提交

## 5/11 WriteUp 流程
- 跑 ctf_agents.writeup.command_recovery 抽 commands.sh 草稿
- 跑 ctf_agents.writeup.generate_writeup 生成 markdown 草稿
- 逐题人工补题面/思路/截图
- 脱敏：cookie / token / 本机路径 / 其它题 flag
- 校对每题"为什么 flag 是对的"
- 5/11 17:30 前提交

## 应急 Takeover 三种情境
1. agent 卡住 → 飞书通知 → operator 手动看 logs/<run>.jsonl 末几行 → 决定 kill switch / 换题 / force_submit
2. 自动提交错误 → 状态自动 wrong+1，第二次错误后自动 freeze；operator 在飞书看 freeze 通知
3. 平台异常（502 / 拒绝连接 / scope 拒绝）→ scope.py 抛 ScopeError → operator 检查白名单 / 平台是否换地址 → 必要时关闭 auto_submit 用 force_submit
```

实际写时根据当前已知信息调整。**关键**：不要发明用户没确认的参数（比如 lookup port、bank 文件名都要用现有路径）。

## 关键不变量（不要破坏）

1. **race fix**：`flag_guard.decide()` 内部逻辑——intent 计算 → 仅当 intent=AUTO_SUBMIT 时调 `state_store.try_claim_submit_slot()` 原子认领；认领失败强制降级 HOLD。覆盖测试在 `tests/test_flag_guard.py:test_concurrent_auto_submit_only_one_wins` 等。
2. **wall-clock state**：`state_store.py` 用 `time.time()` Unix 秒持久化，`_compute_remaining()` 处理时钟回跳保守等满窗口。覆盖测试在 `test_state_durable_across_simulated_restart` 等。
3. **判断题同义词**：`wjx_exam_assist.js` 的 `JUDGE_TRUE_LABELS`/`JUDGE_FALSE_LABELS` + `judgeOptionPolarity()` 按极性分组，必须保持。覆盖测试在 `tests/test_wjx_assist_logic.js`。
4. **风险 notes 拦截**：`isRiskyNote()` 必须包含 `negation_mismatch` / `close_second_candidate` / `single_option_*` / `multi_partial_match` / `manual_review_required:*`。
5. **kill switch 即时性**：文件存在性检查在 `flag_guard.decide()` 入口，不走 state_store（避免锁延迟）。
6. **force_submit 不绕开**：format_ok / scope / rate_limit / reason ≥ 10 字一个都不能少；强日志 redact=False。

## 验证命令

任何修改后 Codex 必须跑全套：

```bash
source tools/env.sh
bash scripts/preflight.sh                 # 期望 29/0/0
bash scripts/run_all_tests.sh             # 期望 ALL CHECKS PASSED
python scripts/rehearsal_5_9.py           # 期望 6 PASS / 0 FAIL / 1 SKIP
```

如果新增 Python 测试：在 `scripts/run_all_tests.sh` 里加一条 `run_step "unit: <name>" python -m unittest tests.<name>`。
如果新增 Node 测试：在 `scripts/run_all_tests.sh` 里加一条 `run_step "unit: <name> (node)" node tests/<name>.js`。

## 文件索引（自上一份 handoff 以来）

新增：

```
ctf_agents/skill/workflow.py
ctf_agents/skill/agents/__init__.py
ctf_agents/skill/agents/mock.py
ctf_agents/skill/agents/misc_real.py
ctf_agents/submit/decisions.py
ctf_agents/submit/state_store.py
ctf_agents/submit/kill_switch.py
ctf_agents/submit/notifications.py        # Codex 上一轮做了
ctf_agents/submit/force_submit.py
scripts/skill_workflow_dryrun.py
scripts/skill_workflow_realctf.py
scripts/rehearsal_5_9.py
scripts/wjx_exam_assist.js
scripts/preflight.sh
scripts/run_all_tests.sh                  # Codex 上一轮做了一部分
tests/test_flag_guard.py
tests/test_skill_workflow.py
tests/test_lookup_service.py
tests/test_wjx_assist_logic.js
package.json
package-lock.json
data/external_ctf/bjdctf2020-misc/*.zip|.rar
docs/codex_next_handoff.md                 # 本文件
```

修改：

```
ctf_agents/knowledge/lookup_engine.py     # race fix + 三分支 + manual_review 透传
ctf_agents/knowledge/lookup_service.py    # 加 /lookup_v2
ctf_agents/knowledge/lookup_cli.py        # 用 LookupEngine 改造
ctf_agents/knowledge/normalize.py         # 新建 (NFKC + 否定词检测)
ctf_agents/knowledge/parse_2020.py        # 新建 (表格 parser)
ctf_agents/knowledge/parse_2024.py        # 新建 (线性 parser)
ctf_agents/submit/flag_guard.py           # rewrite 状态机 + race-safe
ctf_agents/skill/router.py                # 保留原状
configs/config.yaml                       # cloud_llm_allowed=true 等
.env.example                              # 已加 FEISHU_*
.gitignore                                # 加 node_modules/ 等
requirements.txt                          # 加 httpx>=0.28.0
scripts/setup_wsl.sh                      # 加 npm install + chromium
scripts/build_all_banks.py                # Codex 上一轮加了 bank_fixes
```

## 给用户的下一句话（你接手后建议说的）

```
Opus 把 contest_day.md runbook 交给我了。我先读完它的交接文档，
确认现在的代码状态和组委会口径，然后写一份按你工作流定制的
runbook。需要 30-60 分钟。

期间不要改 configs/config.yaml 或任何 ctf_agents/* 的文件——
那是 Opus 已经测过的状态机，我不会动。我只产出文档。

完成后我会跑 bash scripts/run_all_tests.sh 确认没破坏，再交回。
```
