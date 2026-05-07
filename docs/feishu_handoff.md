# 飞书接入交接文档

更新时间：2026-05-07

## 目标

本项目只需要飞书做一件事：把比赛自动化流程中的关键事件推送到你的飞书群里。

当前支持的事件：

- `human_review`：需要人工确认的 flag 候选
- `freeze`：某题连续错误达到阈值后冻结
- `kill_switch`：自动提交被 panic button 降级
- `force_submit`：人工 override 提交后的结果

飞书在这里不是聊天机器人，也不负责接收命令。它只是通知通道。

## 你要准备什么

你需要在飞书里创建一个“自定义机器人”，拿到两样东西：

- `FEISHU_WEBHOOK`：机器人 webhook URL
- `FEISHU_SECRET`：签名校验密钥

不要把这两个值发到聊天里，也不要写进 Markdown 文档。只放在本机 `.env`。

## 飞书端操作

1. 打开比赛通知用的飞书群。
2. 进入群设置。
3. 找到“群机器人”或“机器人”。
4. 添加机器人，选择“自定义机器人”。
5. 机器人名字建议用：`DLUT-CTF Guard`。
6. 安全设置建议开启“签名校验”，复制生成的密钥。
7. 保存后复制 webhook 地址。

安全设置建议：

- 推荐：签名校验。
- 可选：关键词，建议 `DLUT-CTF`。本项目模板都带 `[DLUT-CTF]` 前缀。
- 不建议先开 IP 白名单。WSL/家庭网络/代理出口可能变化，容易误拦截。

飞书官网说明里也建议给 webhook 加安全设置；签名算法是 `timestamp + "\n" + secret`，再做 HMAC-SHA256 和 Base64。本项目的 `ctf_agents/common/feishu.py` 已按这个方式实现。

参考：<https://www.feishu.cn/content/7271149634339422210>

## 本机配置

在项目根目录：

```bash
cd /path/to/dlut-ctf-2026
```

创建或编辑 `.env`：

```bash
FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/你的token'
FEISHU_SECRET='飞书机器人签名密钥'
```

然后把 `configs/config.yaml` 里的飞书开关改成：

```yaml
feishu:
  enabled: true
  mention_user_ids: []
```

注意：`.env` 有值但 `feishu.enabled: false` 时，项目会故意不发消息，只返回 preview。这是为了避免未准备好时误打扰。

## 连通性测试

先测底层 webhook，不经过项目配置：

```bash
source tools/env.sh
python - <<'PY'
import os
from dotenv import load_dotenv
from ctf_agents.common.feishu import send_text

load_dotenv()
webhook = os.environ["FEISHU_WEBHOOK"]
secret = os.environ.get("FEISHU_SECRET", "")
print(send_text(webhook, "[DLUT-CTF] 飞书 webhook 连通性测试", secret=secret))
PY
```

预期：

- 飞书群里收到 `[DLUT-CTF] 飞书 webhook 连通性测试`
- 终端返回里 `ok` 为 `True`

再测项目通知模板：

```bash
source tools/env.sh
python - <<'PY'
import yaml
from dotenv import load_dotenv
from ctf_agents.submit.notifications import notify_kill_switch

load_dotenv()
cfg = yaml.safe_load(open("configs/config.yaml", encoding="utf-8"))
print(notify_kill_switch(cfg["feishu"], activated=True, reason="连通性测试"))
PY
```

预期：

- 飞书群里收到 kill switch 测试消息
- 返回结果里 `sent` 为 `True`

如果返回 `sent: False`，优先检查：

- `configs/config.yaml` 里 `feishu.enabled` 是否为 `true`
- `.env` 是否在项目根目录
- `.env` 变量名是否正好是 `FEISHU_WEBHOOK` 和 `FEISHU_SECRET`
- 机器人是否开启了签名校验但 secret 填错
- 如果设置了关键词，消息里是否包含关键词

## 项目里已经有什么

相关文件：

- `ctf_agents/common/feishu.py`：底层 webhook 发送和签名
- `ctf_agents/submit/notifications.py`：事件模板和分发 helper
- `ctf_agents/submit/force_submit.py`：`force_submit --commit` 已接入通知
- `tests/test_notifications.py`：通知分发测试
- `scripts/run_all_tests.sh`：已纳入通知测试

当前已经实现的 helper：

```python
notify_decision(feishu_cfg, decision)
notify_submit_outcome(feishu_cfg, decision=decision, state_update=state_update, max_wrong=2)
notify_force_submit_result(feishu_cfg, challenge_id=..., flag=..., correct=..., reason=..., actor=...)
```

## Opus 后续要接哪里

普通技能赛 workflow 主循环里需要接两处。

第一处：`guard.decide()` 后。

```python
decision = guard.decide(candidate)
notify_decision(cfg["feishu"], decision)
```

这会处理：

- `HUMAN_REVIEW`
- kill switch 降级到人审

第二处：平台提交后，`guard.record_outcome()` 后。

```python
state_update = guard.record_outcome(
    candidate,
    decision,
    correct=result.correct,
    platform_response=result.message,
)
notify_submit_outcome(
    cfg["feishu"],
    decision=decision,
    state_update=state_update,
    max_wrong=submit_cfg["max_wrong_per_challenge"],
)
```

这会处理：

- 刚刚触发 freeze 的题目

`force_submit --commit` 已经接好，不需要 Opus 再接。

## 验收命令

无真实 webhook 时，跑：

```bash
source tools/env.sh
python -m unittest tests.test_notifications
bash scripts/run_all_tests.sh
```

配置真实 webhook 后，再跑：

```bash
source tools/env.sh
python - <<'PY'
import yaml
from dotenv import load_dotenv
from ctf_agents.submit.notifications import notify_human_review

load_dotenv()
cfg = yaml.safe_load(open("configs/config.yaml", encoding="utf-8"))
print(notify_human_review(
    cfg["feishu"],
    challenge_id="dryrun-web-01",
    category="web",
    score=0.87,
    flag_redacted="flag{d…test}",
    reason="飞书真实 webhook 验收",
))
PY
```

飞书群收到消息，就算飞书接入验收通过。

## 不要做的事

- 不要把 webhook 或 secret 发到聊天里。
- 不要提交 `.env`。
- 不要在日志里打印完整 webhook。
- 不要一开始就开 IP 白名单。
- 不要把飞书当作命令入口；当前只做通知。
