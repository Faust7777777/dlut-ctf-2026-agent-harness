# OpenAI-Compatible Expert Sidecar Runbook

The expert sidecar is a P2, optional, high-cost helper for hard offline
challenges.  It is not part of the supervisor critical path and must
stay default-disabled until the operator enables it locally.

## Contract

Allowed:

```text
read:  artifacts/challenges/<id>/
write: artifacts/challenges/<id>/expert_notes.md
write: artifacts/challenges/<id>/expert_candidates.json
```

Forbidden:

```text
read .env, .secrets, state, logs, cookie jars, webhook config, API keys
call GZCTF APIs
submit flags
modify supervisor, adapter, guard, logs, or state handling
write any output file except expert_notes.md / expert_candidates.json
```

The expert output is advisory:

```text
expert_candidates.json -> sidecar validator -> FlagGuard -> GZCTFAdapter
```

Never:

```text
expert output -> submit
```

## Config

Use this shape when enabling the sidecar in local config:

```yaml
expert_sidecar:
  enabled: false
  provider: deepseek            # or openai / azure_openai
  default_model: "deepseek-v4-pro"
  hard_model: "deepseek-v4-pro"
  api_base_url: "https://api.deepseek.com"
  api_key_env: DEEPSEEK_API_KEY
  reasoning_effort: high
  max_calls_total: 8
  max_calls_per_challenge: 1
  timeout_s: 600
  max_input_files: 20
  max_attachment_mb: 20
  max_preview_chars: 4000
  budget_usd_soft_limit: 30
```

Model names, deployment names, API base URLs, and key environment
variable names are knobs.  Set them only in local config when enabling
the sidecar; do not hard-code contest logic around a model or endpoint.

Provider options:

```yaml
provider: openai        # uses Authorization: Bearer <key>
provider: azure_openai  # uses api-key: <key>; model is the Azure deployment name
provider: deepseek      # uses Authorization: Bearer <key>; model is the DeepSeek model name
```

For OpenAI, configure a Responses API base URL such as a local-only
value ending in `/v1`.  For Azure OpenAI, configure the Azure Responses
API base URL and set `default_model` / `hard_model` to the deployment
name.  For DeepSeek, configure the DeepSeek API base URL and set the
model name to the chosen DeepSeek model, such as `deepseek-v4-pro`.
Azure and DeepSeek remain disabled unless `enabled: true` and all
provider knobs are supplied.

## API Key Handling

The sidecar only reports:

```text
OPENAI_API_KEY=SET
OPENAI_API_KEY=UNSET
```

It must never print or write the key value.  Dry-run mode does not need
an API key and does not call OpenAI.  If a non-default key environment
variable is configured, the sidecar prints only `<KEY_ENV>=SET` or
`<KEY_ENV>=UNSET`.

## Candidate Schema

`expert_candidates.json` uses the existing sidecar schema:

```json
[
  {
    "challenge_id": "123",
    "candidate": "flag{...}",
    "confidence": "high",
    "evidence_paths": [
      "artifacts/challenges/123/expert_notes.md"
    ],
    "submit_recommendation": "never_direct_submit",
    "notes": "short reproducible rationale"
  }
]
```

The validator rejects forbidden keys such as `submit`, `platform_call`,
`secret`, `cookie`, `password`, `api_key`, `token`, `force_submit`, and
`bypass_guard`.

## Dry Run

Run:

```bash
source tools/env.sh
python scripts/openai_expert_sidecar_dryrun.py
```

Expected:

```text
[ALL CHECKS PASSED]
```

The dry-run proves:

```text
schema-compatible mock candidate is accepted
outside challenge artifact paths are rejected
oversized input files are rejected
forbidden evidence paths are rejected
only SET/UNSET API key status is printed
summary is written only inside a temporary sandbox, not logs/
```

## Prompt Template

Use this shape if invoking a live model manually from the solve-first
loop:

```text
You are solving one offline CTF challenge.

Use only the files provided from artifacts/challenges/<id>/.
Do not assume platform access.
Do not submit anything.
Do not read .env, .secrets, state, logs, cookies, tokens, or API keys.
Do not guess. If evidence is insufficient, return no candidate.
A candidate must be backed by concrete file paths and derivation.

Write:
- artifacts/challenges/<id>/expert_notes.md
- artifacts/challenges/<id>/expert_candidates.json

expert_candidates.json must use submit_recommendation =
"never_direct_submit".
```

## Failure Policy

If the sidecar is disabled, over budget, missing `OPENAI_API_KEY`, or
produces invalid candidates, continue without it.  This must not block
the AI identity supervisor or change the contest submit path.
