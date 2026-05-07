# 给 Opus 的飞书上下文交接

更新时间：2026-05-07

## 先说明为什么 Codex 动了代码

这次 Codex 动飞书相关代码，不是为了接管主开发，也不是为了把 `flag_guard` 主线抢过来。原因很简单：review 时发现“飞书通知已完成”的说法只成立了一半。

当时已有的是：

- `ctf_agents/submit/notifications.py` 里的四类消息模板；
- `scripts/run_all_tests.sh` 里的模板 preview。

缺的是：

- guard 决策之后，谁负责把 `HUMAN_REVIEW` / kill switch 降级发出去；
- `record_outcome()` 之后，谁负责把 newly frozen 发出去；
- `force_submit --commit` 之后，谁负责把 override 结果发出去；
- 这些路径有没有测试覆盖。

所以 Codex 只补了一个很薄的通知分发层，并接了已经存在的 `force_submit --commit` CLI。状态机主体、并发提交流程、技能赛 workflow 仍由 Opus 继续负责。

## Codex 已经改了什么

变更文件：

- `ctf_agents/submit/notifications.py`
- `ctf_agents/submit/force_submit.py`
- `tests/test_notifications.py`
- `scripts/run_all_tests.sh`
- `docs/feishu_handoff.md`

新增/可用的 helper：

```python
notify_decision(feishu_cfg, decision)
notify_submit_outcome(
    feishu_cfg,
    decision=decision,
    state_update=state_update,
    max_wrong=submit_cfg["max_wrong_per_challenge"],
)
notify_force_submit_result(
    feishu_cfg,
    challenge_id=...,
    flag=...,
    correct=...,
    reason=...,
    actor=...,
)
```

`force_submit --commit` 已经接了 `notify_force_submit_result()`。

普通 agent/workflow 主循环还没有接，因为这部分主循环还需要 Opus 设计。

## Opus 接下来要接哪里

第一处：`guard.decide()` 后。

```python
from ctf_agents.submit.notifications import notify_decision

decision = guard.decide(candidate)
notify_decision(cfg["feishu"], decision)
```

它会处理：

- `HUMAN_REVIEW`
- kill switch 导致的 `HUMAN_REVIEW`

第二处：平台提交后，`guard.record_outcome()` 后。

```python
from ctf_agents.submit.notifications import notify_submit_outcome

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

它会处理：

- `newly_frozen=True` 的 freeze 通知

第三处：无需再接。

`force_submit --commit` 已经会在提交后发 `force_submit` 通知。

## 当前测试状态

已跑过：

```bash
source tools/env.sh
python -m unittest tests.test_notifications tests.test_flag_guard
bash scripts/run_all_tests.sh
```

结果：

- `tests.test_notifications`：6 个用例通过
- `tests.test_flag_guard`：19 个用例通过
- `run_all_tests.sh`：ALL CHECKS PASSED

## 仍然没有解决的问题

这份交接只解决飞书通知路径，不解决下面两个 review 发现的问题：

1. 并发 `AUTO_SUBMIT` race：`decide()` 和 `record_outcome()` 不在同一把锁内，两个 agent 同时 decide 仍可能同时拿到 `AUTO_SUBMIT`。
2. monotonic state 持久化：state 文件里保存 monotonic 值，但进程重启/WSL 休眠后没有按注释清零。

这两个仍应由 Opus 在 `flag_guard` 主线里继续修。

## 给用户的飞书配置文档

用户侧手把手配置文档在：

```text
docs/feishu_handoff.md
```

用户只需要创建飞书自定义机器人，把 `FEISHU_WEBHOOK` / `FEISHU_SECRET` 放进 `.env`，再把 `configs/config.yaml` 的 `feishu.enabled` 改成 `true`。

