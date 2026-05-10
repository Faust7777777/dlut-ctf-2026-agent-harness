# 本地 GZCTF rehearsal 环境交接

写给接手的 Codex / Opus：目标是在本机自建一个仅绑定 `127.0.0.1` 的 GZCTF，用它验证当前 `GZCTFAdapter` + `scripts/ai_contest_supervisor.py` 主链路。本文档只描述本地 rehearsal，不替代 2026-05-09 真环境联调。

## 结论

本地自建 GZCTF 与真实 DLUT GZCTF 使用同一套 GZCTF 官方 API / OpenAPI，因此可以覆盖 adapter 的核心平台链路：

- `login`
- `profile`
- `team`
- `game`
- `details`
- `challenge`
- `attachment`
- `submit`
- `status`

这个 rehearsal 能验证本仓库当前主链路是否能在 GZCTF 官方接口形态下跑通，包括登录会话、题目同步、静态附件下载、提交结果轮询、`WrongAnswer` 冻结、`Accepted` 停止、重复提交拦截、state/log/heartbeat 写入。

但它不能替代 2026-05-09 真实 DLUT GZCTF 环境验证。真环境仍必须单独确认：

- 校园网 / VPN / DNS / 反向代理可达性。
- SSO、验证码、cookie 复用和登录策略。
- 真实比赛站是否要求 HTTPS、前端加密提交、`submit_payload_mode=encrypted`、`api_public_key` 等。
- 真实 payload mode、真实附件权限、真实动态容器和动态 flag 行为。
- 组织者提供的测试题 / 测试 game 的实际响应格式。

## 官方依据

使用官方资料，不要参考第三方教程作为唯一依据：

- GZCTF Quick Start: `https://gzctf.gzti.me/guide/start/quick-start.html`
  - 官方说明 GZCTF 可通过 Docker image 和 `docker-compose` 部署完整平台。
  - Quick Start 示例包含 `compose.yml`、`appsettings.json`、PostgreSQL、`docker compose up -d`。
  - 官方同时说明该部署方式缺少 HTTPS，适合本地测试，不适合生产。
- 官方 OpenAPI: `https://gzctf.gzti.me/openapi.json`
  - 用来核对 `GZCTFAdapter` 调用路径和响应结构。
- 官方 Scalar API docs: `https://gzctf.gzti.me/scalar.html`
  - 用来人工浏览 API、确认 endpoint、schema 和请求/响应字段。

## 不做范围

本任务只做本地 rehearsal，不要扩大边界：

- 不要碰公开 CTF 站。
- 不要把自动 submit 打到第三方平台。
- 不要用真实 DLUT 账号、真实比赛 cookie、真实 flag 或任何 secrets 写入仓库。
- 不要提交 `.env`、`.secrets/`、cookie jar、`configs/ai_contest.local.yaml`、本地数据库、附件、日志、state。
- 不要设计需要比赛日人工介入的流程。AI identity 主链路要求启动后自主运行。
- 不要绕过 `GZCTFAdapter` 直接 `curl` 提交 flag。
- 不要绕过 `FlagGuard` 直接调用 adapter submit。
- 不要同时跑两个 supervisor 指向同一个本地 GZCTF / state 目录。

## 推荐文件结构

如果需要落地 rehearsal lab，建议只在本地创建这些文件。除非另有明确要求，不要提交本地配置和 secrets。

```text
local/gzctf-lab/
  compose.yml
  appsettings.json
  README.md
  challenges/
    static-attachment/
      attachment.txt

configs/
  ai_contest.local.yaml    # gitignored 或只保留在本地，不提交
```

建议：

- `local/gzctf-lab/compose.yml` 使用官方 `gztime/gzctf:latest` 或 `ghcr.io/gztimewalker/gzctf/gzctf:latest`。
- 端口只绑定本地，例如 `127.0.0.1:8080:8080`，避免暴露到局域网。
- `appsettings.json` 的 `CaptchaConfig.Provider` 设为 `None`，减少本地 rehearsal 变量。
- `ContainerProvider.Type` 可先保留 `Docker`，但本轮主目标是 static attachment challenge，不依赖动态容器。
- `PUBLIC_ENTRY` / `ContainerProvider.PublicEntry` 使用 `127.0.0.1` 或本机可访问地址。
- sample attachment 写入一个可被 agent 识别的测试 flag，例如 `flag{local_static_accept}`，只用于本地 game。

## 前置阅读

接手 agent 执行前必须先读：

1. `runbooks/ai_identity.md`
2. `runbooks/gzctf_adapter.md`
3. `runbooks/guard_policy.md`
4. `runbooks/failure_modes.md`

重点确认：

- 平台 I/O 只能通过 `ctf_agents/submit/gzctf_adapter.py`。
- 提交 gate 只能通过 `ctf_agents/submit/flag_guard.py`。
- `WrongAnswer` 在 AI identity 下是 1-strike freeze。
- `Accepted` 后该 challenge 必须停止。
- duplicate flag hash 必须在本地被拦截。
- heartbeat 至少每 60 秒写入一次。

## 本地搭建目标

本地 GZCTF 需要包含：

- admin 用户。
- player 用户。
- team。
- game。
- 至少一个 static attachment challenge。
- 至少两个可控提交场景：
  - 正确 flag：用于验证 `Accepted`。
  - 错误 flag：用于验证 `WrongAnswer` freeze。

本仓库需要准备：

- `configs/ai_contest.local.yaml` 指向 `http://127.0.0.1:8080` 或实际本地端口。
- `scope.allowed_domains` 只允许本地 host，例如 `127.0.0.1`、`localhost`。
- `.env` 只放本地 player 凭据，不放真实 DLUT 凭据。
- `gzctf.auth_mode: "password"`，避免误用真实 cookie。
- `gzctf.submit_payload_mode: "plaintext"`，本地 rehearsal 优先验证主链路；真环境 payload mode 另测。

最小配置形态示例，按实际 game id 调整：

```yaml
project:
  name: dlut-local-gzctf-rehearsal
  timezone: Asia/Shanghai

gzctf:
  base_url: "http://127.0.0.1:8080"
  game_id: 1
  default_team_id: null
  auth_mode: "password"
  username_env: "GZCTF_USERNAME"
  password_env: "GZCTF_PASSWORD"
  cookie_jar_path: ".secrets/local_gzctf_cookies.json"
  submit_payload_mode: "plaintext"
  api_public_key: ""
  poll_timeout_s: 30.0
  poll_interval_s: 1.0

scope:
  allowed_domains:
    - "127.0.0.1"
    - "localhost"
  allowed_cidrs: []
  deny_public_scan: true

submit:
  adapter: gzctf
  auto_submit: true
  auto_submit_categories: ["misc", "forensics", "crypto"]
  min_conf_auto_submit: 0.92
  min_conf_human_review: 0.70
  max_wrong_per_challenge: 1
  min_seconds_between_submits_global: 1
  min_seconds_between_submits_per_challenge: 1
  flag_regex: "(?i)(flag|dlutctf|dasctf)\\{[^{}\\s]{4,200}\\}"
  state_path: "state/submission_state.local.json"
  kill_switch_file: ".auto_submit_off"
  force_submit_min_reason_length: 10
  pwn_reverse_force_human_review: true

agent:
  enabled_categories: ["misc", "forensics"]
  challenge_loop_interval_s: 5
  challenge_solve_timeout_s: 120
  global_run_timeout_s: 300
  heartbeat_interval_s: 10

paths:
  state_dir: "state/local-gzctf"
  artifacts_dir: "artifacts/local-gzctf"
  logs_dir: "logs/local-gzctf"
  locks_dir: "state/local-gzctf/locks"

feishu:
  enabled: false
  mention_user_ids: []

codex_sidecar:
  enabled: false
  max_parallel_tasks: 1
  timeout_s: 120
  allow_patch: false
  allow_submit: false
  allow_secret_read: false
  artifact_root: "artifacts/local-gzctf/challenges"
```

## 执行步骤

### 1. 启动本地 GZCTF

在 `local/gzctf-lab/` 放置 `compose.yml` 和 `appsettings.json`，按官方 Quick Start 使用 Docker Compose 启动：

```bash
cd local/gzctf-lab
docker compose up -d
docker compose ps
```

浏览器只访问本地：

```text
http://127.0.0.1:8080
```

如果绑定端口不是 `8080`，同步更新 `configs/ai_contest.local.yaml`。

### 2. 初始化 GZCTF 数据

用 admin 完成本地平台配置：

1. 登录初始 admin。
2. 创建 player 用户，或开放注册后注册本地 player。
3. 创建 team，并让 player 加入。
4. 创建 game。
5. 创建 `Misc` 分类 static attachment challenge。
6. 上传 sample attachment。
7. 设置正确 flag，例如 `flag{local_static_accept}`。
8. 确认 game 对 player 可见，challenge 对 player 可见。

建议至少建两个 challenge：

- `local-accepted`：附件内含正确 flag，用于自然 `Accepted`。
- `local-wrong-freeze`：附件内故意放错误 flag，平台正确 flag 设置为另一个值，用于 `WrongAnswer` freeze。

### 3. 配置本地 supervisor

创建本地 `.env`，只写本地账号：

```bash
GZCTF_USERNAME=local_player
GZCTF_PASSWORD=local_player_password
```

创建 `configs/ai_contest.local.yaml`，使用上面的最小配置模板。确认：

- `gzctf.base_url` 是 `http://127.0.0.1:<port>`。
- `gzctf.game_id` 是本地 game id。
- `scope.allowed_domains` 不包含任何公网比赛域名。
- `paths.*` 使用 `local-gzctf` 子目录，避免污染真实 rehearsal / contest state。
- `submit_payload_mode` 先用 `plaintext`。

### 4. 跑 healthcheck-only

```bash
source tools/env.sh
python scripts/ai_contest_supervisor.py \
  --config configs/ai_contest.local.yaml \
  --healthcheck-only
```

预期：

- 退出码为 `0`。
- JSONL 中出现 `healthcheck_ok`。
- `healthcheck_ok` message 包含 player、team、game、challenge 数量。

失败则先查：

- base URL / port。
- 本地账号密码。
- player 是否加入 team。
- game 是否开始或对 player 可见。
- `scope.allowed_domains` 是否包含 `127.0.0.1`。
- GZCTF 容器日志。

### 5. 跑 no-submit rehearsal

目的：先验证同步、附件下载、agent 候选生成、日志和 heartbeat，不触发平台提交。

推荐方式之一：

- 临时在 `configs/ai_contest.local.yaml` 设置 `submit.auto_submit: false`。
- 或把 sample attachment 中 flag 改成低置信 / 非匹配格式。

运行：

```bash
python scripts/ai_contest_supervisor.py \
  --config configs/ai_contest.local.yaml
```

观察到至少一个 heartbeat 后停止。预期：

- `logs/local-gzctf/` 出现 `ai-contest-*.jsonl`。
- `state/local-gzctf/ai_contest_state.json` 出现 challenge state。
- `artifacts/local-gzctf/challenges/<id>/` 出现下载附件。
- 不出现真实 `submit_outcome`，或 guard decision 不是 `AUTO_SUBMIT`。

### 6. 跑 controlled submit rehearsal

恢复 `submit.auto_submit: true`，确保只指向 `127.0.0.1`。

运行：

```bash
python scripts/ai_contest_supervisor.py \
  --config configs/ai_contest.local.yaml
```

分三轮验证：

1. `Accepted stop`
   - challenge 附件含平台正确 flag。
   - 预期出现 `submit_outcome status=Accepted kind=accepted`。
   - state 中该 challenge 为 `accepted`。
   - 后续 tick 不再处理该 challenge。

2. `WrongAnswer freeze`
   - challenge 附件含格式正确但平台错误的 flag。
   - 预期出现 `submit_outcome status=WrongAnswer kind=wrong`。
   - state 中该 challenge 为 `wrong_frozen`，`freeze_reason=wrong_answer`，`wrong_count=1`。
   - 后续 tick 不再提交该 challenge。

3. `duplicate`
   - 重启 supervisor，保持 state 不删。
   - 或让同一 challenge 继续产出同一 candidate。
   - 预期出现 `duplicate_candidate_skipped`，不发生第二次 submit。

### 7. 验证 state / log / heartbeat

检查：

```bash
ls -la logs/local-gzctf
ls -la state/local-gzctf
jq '.challenges' state/local-gzctf/ai_contest_state.json
rg '"event_type":"heartbeat"|"event_type":"submit_outcome"|"event_type":"duplicate_candidate_skipped"' logs/local-gzctf
```

预期：

- heartbeat 按配置间隔持续写入。
- submit outcome 不含明文 flag。
- state 中只记录 flag hash / redacted flag，不保存完整 flag。
- accepted / wrong_frozen / pending 等状态符合 `runbooks/failure_modes.md`。

## 验收标准

必须全部满足才算 rehearsal 通过：

| 编号 | 标准 | 通过信号 |
|---|---|---|
| A1 | 本地 GZCTF 只绑定本地地址 | 浏览器和 config 均使用 `127.0.0.1` / `localhost` |
| A2 | admin/player/team/game/challenge 创建完成 | player 登录后能看到 game 和 challenge |
| A3 | `--healthcheck-only` 通过 | 退出码 `0`，日志有 `healthcheck_ok` |
| A4 | adapter 登录链路通过 | `login/profile/team/game/details` 均无异常 |
| A5 | challenge detail 通过 | supervisor 能拉取单题详情 |
| A6 | attachment 下载通过 | `artifacts/local-gzctf/challenges/<id>/` 有附件 |
| A7 | no-submit 模式不打平台提交 | 无 `submit_outcome` 或 guard 非 `AUTO_SUBMIT` |
| A8 | controlled submit 能拿到 submit id / status | 日志有 `submit_outcome`，status 终态可读 |
| A9 | `Accepted` 后停止 | state 为 `accepted`，后续 tick 不再处理 |
| A10 | `WrongAnswer` 后冻结 | state 为 `wrong_frozen`，`wrong_count=1` |
| A11 | duplicate 被本地拦截 | 日志有 `duplicate_candidate_skipped`，平台无第二次提交 |
| A12 | state/log/heartbeat 正常 | state JSON、JSONL、heartbeat 都存在且持续更新 |
| A13 | secrets 未入仓 | `git status --short` 不出现 `.env`、`.secrets/`、local config、state、logs |
| A14 | 没有公网目标 | config 和日志中不出现公开 CTF 站或真实 DLUT 域名 |

## 失败处理矩阵

| 失败现象 | 可能原因 | 处理 |
|---|---|---|
| `docker compose up -d` 后打不开页面 | 端口冲突、容器未启动、GZCTF 初始化慢 | `docker compose ps`、`docker compose logs -f gzctf`；换本地端口并同步 config |
| GZCTF 反复重启 | `appsettings.json` 数据库连接、`XorKey`、挂载路径错误 | 对照官方 Quick Start 重查 `POSTGRES_PASSWORD`、Database connection、volume |
| admin 无法登录 | 初始数据库已存在、`GZCTF_ADMIN_PASSWORD` 只在首启生效 | 清理本地 lab 数据卷后重建；只清理 `local/gzctf-lab/data`，不要碰仓库其他 state |
| player 看不到 game | team 未加入、game 未开始、权限/可见性未配置 | 用 admin 检查 player/team/game/challenge 状态 |
| `healthcheck failed` | 登录失败、scope 拦截、game id 错误、API 不可达 | 看 JSONL 的 `healthcheck_fail`；确认 `.env`、`scope.allowed_domains`、`gzctf.game_id` |
| `ScopeError` | base URL host 未在 allowlist | 本地只加入 `127.0.0.1` / `localhost`，不要加入公网域名 |
| 附件 403/404 | challenge 未公开、附件 URL 需要登录、adapter session 未复用 | 确认 player 可见；看 `attachment_error`；用 adapter session 路径排查，不要裸 curl 提交 |
| `submit_payload_mode` 报错 | 本地配置误设 `encrypted` | 本地改回 `plaintext`；真实环境加密提交另列 2026-05-09 验证项 |
| submit 无 submit id | GZCTF 响应结构与 adapter 提取逻辑不匹配 | 保存本地响应摘要，补 `_extract_submit_id()` 测试；不要扩大到公网 |
| status 一直 `FlagSubmitted` | 平台判题慢、poll timeout 太短 | 增大本地 `poll_timeout_s`；确认 challenge flag 设置；不重复提交 |
| `WrongAnswer` 后仍继续提交 | state 未持久化、重启前删了 state、不同 challenge id | 保留 `state/local-gzctf` 复测；检查 `wrong_frozen` 和 `submitted_flag_hashes` |
| duplicate 没被拦截 | state 路径变化、flag 文本不同、challenge id 不同 | 固定 `paths.state_dir`；确认 candidate flag 完全一致；查 `submitted_flag_hashes` |
| heartbeat 不出现 | 运行时间太短、`heartbeat_interval_s` 太大、进程异常退出 | 本地把 heartbeat 调到 10s；看 exit code 和 JSONL 尾部 |
| `state_corrupt_rotated` | 上次异常写入或手工编辑 state | 接受自动 rotate；不要手工改 state；从 fresh state 复测 |
| 误指向真实域名 | config 复用了真实 `ai_contest.yaml` | 立即停止进程；检查日志确认无 submit；只使用 `configs/ai_contest.local.yaml` |

## 给接手 Codex / Opus 的执行提示词

把下面提示词交给实际执行的 agent。要求其先读 runbooks，只在本地 `127.0.0.1` GZCTF 上测试。

```text
你接手的是 /home/wuwai/dlut-ctf-2026 的本地 GZCTF rehearsal。先读 runbooks/ai_identity.md、runbooks/gzctf_adapter.md、runbooks/guard_policy.md、runbooks/failure_modes.md，再执行。只允许在本地 127.0.0.1 / localhost 的自建 GZCTF 上测试，不要访问公开 CTF 站，不要访问真实 DLUT GZCTF，不要把自动 submit 打到第三方平台。所有平台 I/O 必须经过 ctf_agents/submit/gzctf_adapter.py，所有提交必须经过 FlagGuard，不允许手写 curl/requests/browser submit。

请在 local/gzctf-lab/ 准备 GZCTF docker compose rehearsal 环境，按官方 Quick Start 使用 compose.yml 和 appsettings.json 启动本地 GZCTF。创建 admin/player/team/game，以及至少一个 Misc static attachment challenge。创建 configs/ai_contest.local.yaml，但不要提交 secrets、本地账号密码、cookie、state、logs、artifacts。配置 base_url=http://127.0.0.1:<port>、scope.allowed_domains 只含 127.0.0.1/localhost、auth_mode=password、submit_payload_mode=plaintext。

依次验证：
1. python scripts/ai_contest_supervisor.py --config configs/ai_contest.local.yaml --healthcheck-only
2. no-submit 模式：验证 game/details/challenge/attachment/state/log/heartbeat，不发生平台 submit
3. controlled submit 模式：验证 Accepted stop、WrongAnswer freeze、duplicate skip、state/log/heartbeat

验收时输出具体证据：退出码、关键 JSONL event、state 中 accepted/wrong_frozen/duplicate/heartbeat 的字段。若失败，按 docs/local_gzctf_rehearsal_handoff.md 的失败处理矩阵定位。不要 revert 或覆盖其他 agent 的改动。
```

## 最终交付要求

接手 agent 完成 rehearsal 后，应提交一份简短结果摘要，至少包含：

- 本地 GZCTF URL，仅限 `127.0.0.1` / `localhost`。
- 使用的 GZCTF image tag。
- 本地 game id 和 challenge id。
- healthcheck 命令与退出码。
- no-submit 证据。
- controlled submit 证据。
- `Accepted`、`WrongAnswer`、duplicate、heartbeat 的日志事件名和 state 字段。
- 未验证项，尤其是真环境 SSO、校园网、验证码、真实 payload mode、动态容器 / 动态 flag。
