# Reverse/Pwn 公开题本地 GZCTF Bundle

## 范围与隔离

- 负责人：worker-C。
- 题目数量：4 题，Reverse 2 题，Pwn 2 题。
- 写入范围：`artifacts/public-ctf-platform/rev-pwn/`、`artifacts/challenges/public-rp-*/`、`docs/public_ctf_platform_rev_pwn.md`。
- 未访问真实 DLUT/GZCTF，未向任何公开平台提交，未攻击远程服务。
- 未读取 `.secrets/`、`state/`、`logs/`。
- `challenge.json` 是本地 GZCTF 导入 manifest，包含 `expected_flag`；solver 阶段不得读取该文件。solver 应只接收题目附件、`solver_scope.txt` 和必要的本地分析文件。

## 题源表

| id | name | category | source_url | attachment_path | expected_flag_source |
|---|---|---|---|---|---|
| public-rp-1 | Transformation (picoCTF 2021) | Reverse | https://github.com/HHousen/PicoCTF-2021/tree/master/Reverse%20Engineering/Transformation | `artifacts/challenges/public-rp-1/attachment/transformation.zip` | 本地运行 `attachment/script.py` 解码 `attachment/enc` |
| public-rp-2 | keygenme-py (picoCTF 2021) | Reverse | https://github.com/HHousen/PicoCTF-2021/tree/master/Reverse%20Engineering/keygenme-py | `artifacts/challenges/public-rp-2/attachment/keygenme-py.zip` | 本地按 `keygenme-trial.py` 的 SHA-256 索引校验逻辑推导 |
| public-rp-3 | ret2win x86_64 (ROP Emporium) | Pwn | https://ropemporium.com/challenge/ret2win.html | `artifacts/challenges/public-rp-3/attachment/ret2win-player.zip` | 官方 `ret2win.zip` 内 `flag.txt`；本 bundle 将其移入 `runtime/flag.txt` 供本地验证，不放入 player attachment |
| public-rp-4 | split x86_64 (ROP Emporium) | Pwn | https://ropemporium.com/challenge/split.html | `artifacts/challenges/public-rp-4/attachment/split-player.zip` | 官方 `split.zip` 内 `flag.txt`；本 bundle 将其移入 `runtime/flag.txt` 供本地验证，不放入 player attachment |

## 每题结果

| id | candidate | confidence | validator | expected_match | 证据摘要 | 失败原因 |
|---|---|---|---|---|---|---|
| public-rp-1 | `picoCTF{16_bits_inst34d_of_8_26684c20}` | high | PASS | true | `script.py` 对 `enc` 逐字符拆高低字节，输出完整 flag；见 `evidence/local_decode.txt` | 无 |
| public-rp-2 | `picoCTF{1n_7h3_\|<3y_of_f911a486}` | high | PASS | true | `sha256(b"GOUGH")` 后取索引 4,5,3,6,2,7,1,8 得 `f911a486`；见 `evidence/local_derivation.txt` | 无 |
| public-rp-3 | `ROPE{a_placeholder_32byte_flag!}` | high | PASS | true | 40 字节 padding + `ret` 对齐 + `ret2win`，本地进程打印 flag；见 `evidence/local_process_ret2win.txt` | 无 |
| public-rp-4 | `ROPE{a_placeholder_32byte_flag!}` | high | PASS | true | 40 字节 padding + `ret` + `pop rdi; ret` + `/bin/cat flag.txt` + `system@plt`，本地进程打印 flag；见 `evidence/local_process_split.txt` | 无 |

## 本地 GZCTF 导入适配

四题均适合导入本地 GZCTF。Reverse 题可作为静态附件题创建；Pwn 题建议上传不含 flag 的 player zip，同时由本地演练 harness 在验证时把内部 `runtime/flag.txt` 放入进程 cwd。这样不需要公开远程服务，也不需要连接外部平台。

本地 platform coordinator 创建题时需要读取每题 `challenge.json` 的字段：

- `title`：题目标题。
- `category`：`Reverse` 或 `Pwn`。
- `description`：题面描述，已写明离线/本地限制。
- `source_url`：公开来源。
- `expected_flag`：本地 GZCTF exact flag。
- `expected_flag_source`：expected flag 的可核对来源。
- `attachment_relpath`：相对题目目录的附件 zip 路径。

导入时还需要 coordinator 自己补平台字段，例如分值、标签、是否隐藏、是否允许附件下载；这些不是本 bundle 的必填字段。

## 汇总

- attempted：4
- correct：4
- wrong：0
- no_candidate：0
- offline_accuracy：100%
- 本地 GZCTF import-ready：4/4

## 猜测迹象

未发现猜测迹象。四个 candidate 都有本地证据：两个 Reverse 来自源码/编码文件推导，两个 Pwn 来自本地进程运行输出。`codex_candidates.json` 全部通过 `ctf_agents/sidecar/codex_validator.py`。

## 泄漏检查

- 未读取 `.secrets/`、`state/`、`logs/`。
- 未输出 cookie、webhook、password 或其他凭据值。
- 公开题 flag 只写在允许范围内的 `challenge.json`、`codex_candidates.json`、证据文件和本报告中。

## 校验命令与结果

候选 schema、附件路径、证据路径和 expected match 校验：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from ctf_agents.sidecar.codex_validator import validate_codex_candidate
root = Path('.')
for cid in [f'public-rp-{i}' for i in range(1,5)]:
    base = root / 'artifacts' / 'challenges' / cid
    challenge = json.loads((base / 'challenge.json').read_text())
    cand = json.loads((base / 'codex_candidates.json').read_text())[0]
    assert (base / challenge['attachment_relpath']).exists()
    assert not validate_codex_candidate(cand, expected_challenge_id=cid)
    assert all((root / p).exists() for p in cand['evidence_paths'])
    assert cand['candidate'] == challenge['expected_flag']
PY
```

结果：4/4 attachment exists，4/4 validator PASS，4/4 evidence exists，4/4 expected_match true。

最终完整 verification 还检查了：

- 每题必备文件存在。
- `challenge.json` 只有指定字段。
- pwn player zip 不包含 `flag.txt`。
- pwn 本地 runtime harness 能用 `runtime/flag.txt` 打印 expected flag。
- reverse 本地解码/推导仍匹配 expected flag。

最新输出：

```text
public-rp-1 Reverse attachment/transformation.zip PASS
public-rp-2 Reverse attachment/keygenme-py.zip PASS
public-rp-3 Pwn attachment/ret2win-player.zip PASS
public-rp-4 Pwn attachment/split-player.zip PASS
ALL_PUBLIC_RP_BUNDLES_VERIFIED
```

## 结论

这组题对比赛日本地演练有参考价值：Reverse 覆盖脚本/编码还原和源码 keygen 逻辑；Pwn 覆盖 x86_64 栈溢出、ret2win、ROP 参数设置和栈对齐。Pwn 题均为本地 process 题，可在无远程服务的 GZCTF 题面中发放附件并要求提交本地推导出的 flag。
