# Opus Next Handoff - 2026-05-07

本文用于给 Opus 4.7 接续主开发。Codex 当前角色是 review、测试、小修和验收，不接管主开发。

## 2026-05-08 重要更新：技能赛改按 AI 身份准备

先读新文档：

- `docs/opus_ai_identity_handoff.md`

用户和 Codex 已根据 Pro 分析重新定方向：**技能赛现在按 AI 身份路线准备，最终是否选择 AI 身份由 5/9 真实 GZCTF 单 Prompt 彩排决定。**

旧文档里“学生身份/人类操作/runbook 优先”的描述不要作为当前技能赛主线。当前 P0 是：

1. GZCTF adapter 最小闭环。
2. `ai_contest_supervisor.py` 确定性状态机。
3. guard 与 supervisor 集成。
4. mock + 真实 GZCTF single-prompt rehearsal。
5. 5/9 Go/No-Go 报告。

Codex 已确认 GZCTF API 文档可用：`https://gzctf.gzti.me/scalar.html`。接口不是未知项；5/9 主要验证真实部署鉴权、附件、容器、submit payload mode（plaintext/encrypted）和 status polling。

## 当前结论

项目已经从“能不能做”进入“彩排和赛前冻结”阶段。

已验证的主线：

- 知识赛题库：5 份 PDF 已入库，`question_bank_merged.json` 共 2815 题。
- 知识赛 lookup：单选、多选、判断三分支可用，乱序选项可映射，smoke 13/13。
- 问卷星考试 MVP：真实问卷星页面已跑通密码、个人信息、单选、多选、判断、提交，结果页 10/10、50/50。
- flag_guard：状态机、kill switch、freeze、force_submit、rate limit race、持久化时间戳已测试通过。
- skill workflow：mock 7 路径通过，真实 BJDCTF Misc A2 已通过。
- 飞书：webhook 本地已配，消息 helper 和测试已接入，但真实 webhook 不应在聊天里再暴露。

## 关键验收证据

### 问卷星

背景：

知识赛平台大概率是学校问卷星考试。Codex 已经先做了一个窄 MVP，并在用户自己创建的真实问卷星考试中验证通过。Opus 当前如果不知道这条线，请先读本节再改代码。

当前不是“设想阶段”，而是已经真实跑通过：

- 第一次普通样例：`https://v.wjx.cn/vm/wdtFJl7.aspx#`
  - 2 道客观题
  - 单选、多选自动点击
  - 自动提交后跳转 `completemobile2.aspx`
  - 页面显示“您的答卷已经提交，感谢您的参与！”
- 第二次真实题库样例：`https://ks.wjx.com/vm/wPiJl4n.aspx#`
  - 10 道来自 `question_bank_merged.json` 的题
  - 覆盖单选、多选、判断
  - 含问卷访问密码
  - 含问卷内个人信息字段：姓名、工号
  - 自动作答并提交
  - 结果页满分

验证链接：

- `https://ks.wjx.com/vm/wPiJl4n.aspx#`

结果页摘要：

- `Correct 10 questions:10`
- `50 total points:50`

当前 MVP 文件：

- `scripts/wjx_exam_mvp.js`

MVP 已支持：

```bash
node scripts/wjx_exam_mvp.js \
  --url 'https://ks.wjx.com/vm/wPiJl4n.aspx#' \
  --answers examples/dlut_bank_wjx_import_corrected_answers.json \
  --password-env WJX_TEST_PASSWORD \
  --identity '{"姓名":"测试用户","工号":"20260001"}' \
  --headless \
  --submit
```

说明：

- `--answers` 当前还是静态答案表，工程化时要替换为 `lookup_service`。
- `--password-env` 只从环境变量读取密码，不应把密码写入命令日志或 JSONL。
- `--identity` 用 label 包含匹配，例如 label 含 `姓名` 就填身份 JSON 里的 `姓名`。
- `--submit` 必须显式传，默认不提交。
- `--dry-run` 只抽题和匹配，不点击选项。

已覆盖能力：

- 密码页：`#txtPassword` + `#btnContinue`
- 个人信息：按 label 包含 `姓名` / `工号` 填写
- 题块：`.field.ui-field-contain`
- 选项：真实 `input` 是 `display:none`，必须点击 `.jqradio` / `.jqcheck` / `.label`
- 提交：`#ctlNext`

### 问卷星真实 DOM 结论

密码页：

- URL 会跳到类似：

```text
https://ks.wjx.com/wjx/join/VerifyPasswordMobile2.aspx?q=...&returnUrl=...
```

- 密码输入框：`#txtPassword`
- 下一步按钮：`#btnContinue`
- 密码用过后页面会显示：

```text
This password has been used.
```

工程化脚本必须识别这条文本，明确提示“密码已使用/需要新密码或后台允许重复答题”，不要继续跑空题。

考试页：

- 题目块：`.field.ui-field-contain`
- 常见 id：`div1`, `div2`, `div3`...
- 如果有个人信息，个人信息题会占用前几个 `div`，真实客观题未必从 `div1` 开始。
- 因此不要写死 `divN == 第 N 题`，必须从题干文本中解析题号或按 `.field` 过滤题型。

单选：

```html
<input type="radio" id="q1_3" name="q1" style="display:none;">
<a class="jqradio" href="javascript:;"></a>
<div class="label" for="q1_3">C、119</div>
```

多选：

```html
<input type="checkbox" id="q2_5" name="q2" style="display:none;">
<a class="jqcheck" href="javascript:;"></a>
<div class="label" for="q2_5">E、美索不达米亚文明</div>
```

关键点：

- 不能点隐藏 `input`，Playwright 会卡在不可见元素。
- 应该点同一 option 容器里的 `.jqradio` / `.jqcheck`，或者 `.label`。
- 点击后检查对应 input 是否 `checked=true`，作为点击成功判据。

提交：

- 提交按钮：`#ctlNext`
- 提交成功可能跳转：

```text
/wjx/join/completemobile2.aspx?activityid=...
```

- 满分样例结果页片段：

```text
Correct 10 questions:10
50 total points:50
```

### 问卷星个人信息/准入三种情况

用户明确说比赛时个人信息可能有三种方式：

1. 短信验证码
2. 问卷里面收集
3. 密码

当前已验证：

- 方式 2：问卷内收集姓名/工号，脚本已能填。
- 方式 3：问卷访问密码，脚本已能进。

方式 1 的建议：

- 不自动化短信验证码。
- 脚本检测到手机号/验证码/获取验证码页面时，暂停并提示用户人工完成。
- 用户人工完成短信验证码进入考试页后，脚本轮询等待 `.field.ui-field-contain` 出现。
- 题块出现后继续自动填个人信息、lookup、点击。

建议工程化参数：

```bash
node scripts/wjx_exam_assist.js \
  --url '考试链接' \
  --password-env WJX_EXAM_PASSWORD \
  --identity '{"姓名":"张三","工号":"学号或工号"}' \
  --lookup-url 'http://127.0.0.1:8765/lookup' \
  --auto-select \
  --no-submit
```

如果是短信验证码：

```bash
node scripts/wjx_exam_assist.js \
  --url '考试链接' \
  --identity '{"姓名":"张三","工号":"学号或工号"}' \
  --lookup-url 'http://127.0.0.1:8765/lookup' \
  --wait-human-auth \
  --auto-select \
  --no-submit
```

行为：

- 打开页面；
- 如果检测到验证码页，打印提示并等待；
- 用户人工完成验证；
- 脚本发现题块后继续。

### 问卷星导入格式坑

重要修正：

判断题导入问卷星时，不能把答案写在题干里，例如 `（错）`。必须标在选项行：

```text
对
错(正确答案)
```

同理，单选/多选必须把 `(正确答案)` 标在选项行：

```text
1.在互联网信息内容管理中，市场环境信息属于（）信息。[单选题]
A、国家外部信息
B、国家内部信息
C、组织外部信息(正确答案)
D、组织内部信息
```

多选：

```text
4.信息通常包括在网络上传输的（）[多选题]
A、消息(正确答案)
B、符号(正确答案)
C、数据(正确答案)
D、信号(正确答案)
E、资料(正确答案)
```

错误示例：

```text
9.国家内部信息包括但不限于国家贸易、战争等信息。（错）[判断题]
对
错
```

这会导致问卷星没有正确识别答案，甚至可能默认或误识别。Codex 曾因此看到第 9/10 题被判 Wrong Answer，后改成 `错(正确答案)` 后结果页 10/10。

相关文件：

- `scripts/wjx_exam_mvp.js`
- `examples/dlut_bank_wjx_import_corrected_answers.json`
- Windows 桌面示例：`C:\Users\15892\Desktop\dlut_bank_wjx_import_corrected.txt`

其它相关文件：

- `examples/dlut_bank_wjx_import_corrected.txt`
- `examples/dlut_bank_wjx_sample.txt`：旧格式示例，不建议继续用
- `examples/dlut_bank_wjx_sample_answers.json`
- `logs/wjx-wPiJl4n-submit.png`
- `logs/wjx-wPiJl4n-submit.jsonl`

### wjx_exam_assist 工程化细节

当前 MVP 是 `scripts/wjx_exam_mvp.js`，后续请不要直接把 MVP 当最终赛中工具。建议新建或重构为 `scripts/wjx_exam_assist.js`。

必须替换静态答案表：

- MVP 用 `--answers *.json`。
- 工程化版本应把当前页题干和选项发给本地 lookup。
- 推荐复用 `LookupEngine` 或补一个 HTTP endpoint，返回：

```json
{
  "matched": true,
  "qid": "2020-content-0007",
  "branch": "single",
  "stem_score": 98.5,
  "answer_letters": ["C"],
  "notes": []
}
```

自动点击策略：

- `stem_score >= 92`
- 无 `negation_mismatch`
- 无 `manual_review_required:*`
- 多选时所有正确选项都映射成功
- 满足以上才自动点击
- 否则只高亮并写日志，不点击

日志字段建议：

```json
{
  "event_type": "wjx_answer_decision",
  "question_no": 7,
  "stem_excerpt": "...",
  "qid": "2020-content-0001",
  "branch": "judge",
  "score": 98.5,
  "answer_letters": ["对"],
  "auto_clicked": true,
  "notes": [],
  "ts": "..."
}
```

敏感字段规则：

- 不记录密码。
- 不记录短信验证码。
- 不记录手机号全量，必要时只记录脱敏后四位。
- 身份信息只记录字段已填，不记录真实值。

### 问卷星导入稿、原题库、运行时兜底三层分离

后续不要把下面三层混成一层：

> 详细约束见本文件 `docs/opus_next_handoff.md` 的这一节。Opus 先读这里，再看聊天摘要，避免把原 bank、导入稿和运行时兜底混在一起。

1. **原始题库层**
   - 来源是 PDF 解析后的 `question_bank_merged.json`
   - 这里保留解析结果和原始答案，不因为某个问卷星测试卷改动

2. **问卷星导入稿层**
   - 这是给学校/测试问卷用的单独导入文本或答案表
   - 它可以和原始 bank 不同，因为人工导入时可能改过、修过、补过
   - 如果某份问卷星测试卷里某题的后台真值和原 bank 不一致，那说明导入稿层和原 bank 不一致
   - 这种情况不要回写原 bank，应该单独修导入稿或在该卷配置里做 override

3. **运行时兜底层**
   - 这是 `wjx_exam_assist.js` 在真实问卷星页面上的行为
   - 优先级建议如下：

```text
verified_override > lookup_service > static fallback > LLM suggestion > human review
```

   - `verified_override` 只用于已确认的问卷星卷内真值冲突，例如某题在该卷里和原 bank 不同
   - `lookup_service` 仍是主路径，按题干/选项匹配出答案
   - `static fallback` 只用于本地同 bank 导入的题目，解决低分或无匹配
   - `LLM suggestion` 只做兜底建议，不直接改 bank，不直接提交
   - 只要和已验证真值冲突，直接人工确认

### 大模型兜底建议

LLM 建议做成“建议器”，不是“拍板器”：

- 只在这些情况触发：
  - `lookup_no_match`
  - `no_answer_letters`
  - `stem_score` 太低
  - 少量 `single_option_close_second`
- 明确不触发：
  - `negation_mismatch`
  - `manual_review_required:*`
- 输出只要结构化 JSON，不要直接写页面：

```json
{
  "qid": "2024-college-0128",
  "suggested_answer": ["C"],
  "confidence": 0.64,
  "needs_human_review": true,
  "reason": "page/import mismatch"
}
```

- 规则建议：
  - LLM 和确定性结果一致且置信度足够高，才允许自动高亮/点击
  - 只要冲突，就进入人工确认
  - 不自动提交

### 对这类冲突题的处理口径

后续不要把任何这类冲突题当成“原始 PDF 题库错了”。

- 原始 bank 保持不动
- 如果这份问卷星测试卷的后台真值和原 bank 不一致，那是导入稿/卷内配置的问题
- 需要单独修导入稿，不回写原 bank
- 运行时如果没有 override，就人工确认

### A2 真实 Misc

Opus 上轮报告：

- `你猜我是个啥`：真 BJDCTF flag `flag{i_am_fl@g}`，`AUTO_SUBMIT score 0.95`
- `藏藏藏`：疑似误报 `flag{xxxxx}`，`HUMAN_REVIEW score 0.91`，guard 拦截正确
- `签个到`：`no_candidate`
- `认真你就输了`：`no_candidate`

相关文件：

- `ctf_agents/skill/agents/misc_real.py`
- `scripts/skill_workflow_realctf.py`
- `scripts/run_all_tests.sh`

## 接下来优先级

### P0 - 写 5/9 一键彩排脚本

请优先做。

目标：把 5/9 必演练场景变成可重复命令，而不是靠人工记忆。

建议新增：

- `scripts/rehearsal_5_9.py`

必须覆盖：

1. mock workflow 全路径：`auto_submit / hold / human_review / reject / no_candidate`
2. kill switch：创建 `.auto_submit_off` 后，misc 高置信从 `AUTO_SUBMIT` 降级 `HUMAN_REVIEW`
3. freeze：同题两次 wrong 后 frozen，第三次强制人审
4. force_submit：frozen 状态下带 reason 触发 override，仍受限频
5. rate limit：连续两个高置信 auto 类别，第二个 `HOLD rate_limit_global`
6. 飞书 preview：无 webhook 时 preview，有 webhook 时真发
7. 问卷星 dry-run：可选，若提供 URL/password/env，则只做 dry-run，不默认提交

验收：

```bash
bash scripts/run_all_tests.sh
python scripts/rehearsal_5_9.py
```

两者都必须绿。`rehearsal_5_9.py` 输出 JSON summary，并写 `logs/rehearsal-*.jsonl`。

### P0 - 工程化问卷星 assist

当前 `wjx_exam_mvp.js` 是 MVP，可以跑通，但还不是赛中工具。

请工程化为：

- `scripts/wjx_exam_assist.js` 或 `ctf_agents/knowledge/wjx_exam_assist.*`

必须做：

1. 接 `lookup_service`，不要依赖静态 answers JSON。
2. 支持 `--password-env`，不要把密码写日志。
3. 支持 `--identity '{"姓名":"...","工号":"..."}'`。
4. 支持短信验证码人工接管：
   - 检测到手机号/验证码页时提示人工完成；
   - 轮询等待 `.field/.ui-field-contain` 题块出现；
   - 题块出现后继续 lookup 和点击。
5. 默认 `auto_select=true`，`auto_submit=false`。
6. 低信题、高风险题、多选映射不完整时只高亮/记录，不自动点击。
7. 所有动作写 JSONL：题干摘要、匹配 qid、score、建议答案、是否点击、是否提交。

验收：

- 用真实问卷星样例链接 dry-run 10/10。
- 用显式 `--submit` 在测试问卷跑 10/10。
- 密码已使用时识别 `This password has been used` 并明确退出。

### P1 - 赛前 runbook

建议新增：

- `runbooks/contest_day.md`

内容包括：

- 12:00 冷启动检查
- `.env` / 飞书 / API key / 磁盘 / kill switch 检查
- 知识赛问卷星模式：短信验证码人工过，题目自动答
- 技能赛分流：Misc/Forensics 可 auto，Web 人审，Pwn/Reverse 人审
- 最后 30 分钟策略
- 日志归档和 writeup 启动命令

### P1 - 组委会问询稿

如果还没发，请补：

- `docs/organizer_inquiry.md`

问题建议：

1. 知识赛平台是否为问卷星考试
2. 是否需要手机验证码/密码/账号登录
3. 是否允许切屏
4. 是否随机抽题和选项乱序
5. 是否可返回修改
6. 技能赛平台类型：CTFd/GZCTF/自研
7. flag 提交限频/错题冻结规则
8. WriteUp 提交格式和截止时间

## 需要用户拍板

当前建议：

1. 5/9 彩排脚本：做。优先级 P0。
2. 问卷星 assist 工程化：做。优先级 P0。
3. lookup_cli 的剪贴板 UX：暂缓。问卷星页内自动答优先级更高。
4. A2 真实公开题继续扩展：暂缓。已有真 Misc 端到端证据，先彩排。
5. 平台 adapter：等组委会公布平台类型后再做。

## 风险清单

### 问卷星验证码

短信验证码不要自动化。方案是人工完成验证码后，脚本等待题块出现继续作答。

### 密码一次性

问卷星可能提示：

```text
This password has been used.
```

脚本必须识别并退出，提示换密码或后台允许重复答题。

### 自动提交

知识赛默认不自动提交。只有测试问卷或用户显式传 `--submit` 才提交。

### 飞书 secret

用户曾在聊天里暴露过 webhook 和 secret。比赛前最好轮换一次。不要再把真实 secret 写入聊天。

### 日志脱敏

技能赛 force_submit 日志可完整记录 flag；问卷星密码、手机号、验证码不得入日志。

## 建议给用户的下一句

请用户拍：

```text
我建议接下来先做两件 P0：
1. 5/9 rehearsal_5_9.py 一键彩排脚本；
2. wjx_exam_assist 工程化，接 lookup_service，支持密码/短信人工接管/个人信息/默认不提交。

A2 真实公开题已经有端到端证据，先不继续扩题。lookup_cli 剪贴板 UX 暂缓。
```
