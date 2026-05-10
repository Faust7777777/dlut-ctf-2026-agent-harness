# OpenAI Expert Sidecar Handoff

> For Opus/Codex: this is a deferred P2 design note. Do not start this until the current local GZCTF public-challenge platform rehearsal is finished.

## Goal

Add an optional high-cost expert sidecar that can call OpenAI API models for hard challenges, while preserving the current safety architecture:

```text
expert model output -> candidate file -> validator -> FlagGuard -> GZCTFAdapter -> submit/status
```

The expert sidecar must never become a submit path.

## Current Context

The existing contest architecture is:

- `scripts/ai_contest_supervisor.py`: deterministic state machine.
- `ctf_agents/submit/gzctf_adapter.py`: only platform I/O.
- `ctf_agents/submit/flag_guard.py`: only submit gate.
- `ctf_agents/sidecar/codex_validator.py`: sidecar candidate schema and sandbox validation.
- Codex sidecar is optional and default disabled.
- Feishu is now enabled locally and `notify_accepted` exists for local testing.

Current P0 remains:

```text
Real GZCTF 5/9 healthcheck + submit/status + 30-min unattended
```

This expert sidecar is not P0.

## Recommended Positioning

Use OpenAI API models as a last-resort expert:

```text
default solving: built-in Misc/Forensics agent + Claude/Opus + Codex sidecar
hard challenge: gpt-5.5 with high/xhigh reasoning
last 1-2 expensive attempts: gpt-5.5-pro
```

Do not make `gpt-5.5-pro` the normal path. It is slower and more expensive, so it should only run when the local loop is stuck and the challenge has enough evidence to justify cost.

## Safety Rules

Hard requirements:

1. Expert sidecar may only read `artifacts/challenges/<challenge_id>/`.
2. It must not read `.env`, `.secrets`, `state`, `logs`, cookie jars, webhook config, or platform credentials.
3. It must not call GZCTF APIs.
4. It must not submit flags.
5. It must not modify `FlagGuard`, adapter, or submission state.
6. It must write only:
   - `artifacts/challenges/<id>/expert_notes.md`
   - `artifacts/challenges/<id>/expert_candidates.json`
7. Candidate output must reuse the existing sidecar schema or a schema that is strictly compatible with `ctf_agents/sidecar/codex_validator.py`.
8. Full flags may appear in `expert_notes.md` and `expert_candidates.json` as solving artifacts, but must not appear in `logs/`, `state/`, or `.secrets/`.
9. Default config must be disabled.
10. All API key checks must report only `OPENAI_API_KEY=SET/UNSET`, never the value.

## Trigger Policy

The supervisor or `/loop` should only request the expert sidecar when all are true:

```text
challenge has been downloaded
challenge is not accepted / pending / frozen
no high-confidence Codex candidate exists
built-in agent returned no candidate or only advisory evidence
challenge has been stuck for at least 8-15 minutes
budget and call limits allow it
category is allowed
```

Recommended allowed categories:

```yaml
allowed_categories:
  - misc
  - forensics
  - crypto
  - reverse
  - web
```

Keep Pwn disabled by default unless the task is purely local binary analysis. Pwn often needs iterative process interaction and can burn time/cost quickly.

## Proposed Config

Add this to `configs/ai_contest.example.yaml` only after tests exist:

```yaml
expert_sidecar:
  enabled: false
  provider: deepseek
  default_model: deepseek-v4-pro
  hard_model: deepseek-v4-pro
  api_base_url: "https://api.deepseek.com"
  api_key_env: DEEPSEEK_API_KEY
  reasoning_effort: high
  max_calls_total: 8
  max_calls_per_challenge: 1
  timeout_s: 600
  max_input_files: 20
  max_attachment_mb: 20
  budget_usd_soft_limit: 30
  allowed_categories:
    - misc
    - forensics
    - crypto
    - reverse
    - web
  disallowed_paths:
    - .env
    - .secrets
    - state
    - logs
```

## Proposed Files

Create:

- `ctf_agents/sidecar/openai_expert.py`
  - Builds a safe challenge bundle from `artifacts/challenges/<id>/`.
  - Applies file count and size limits.
  - Calls OpenAI only when enabled and API key is set.
  - Supports a mock mode for tests.
  - Writes `expert_notes.md` and `expert_candidates.json`.

- `scripts/openai_expert_sidecar_dryrun.py`
  - Runs a mock expert response against a fixture challenge.
  - Verifies schema, sandbox paths, budget limits, and no forbidden reads.

- `tests/test_openai_expert_sidecar.py`
  - Tests config disabled path.
  - Tests missing API key path.
  - Tests sandbox path restriction.
  - Tests max files and max MB restriction.
  - Tests mock response writes valid candidate files.
  - Tests forbidden keys are rejected by the existing validator.

- `runbooks/openai_expert_sidecar.md`
  - Operator instructions.
  - Budget policy.
  - Prompt template.
  - Failure modes.

Modify only after tests:

- `configs/ai_contest.example.yaml`
  - Add disabled `expert_sidecar` block.

Optional later:

- `scripts/ai_contest_supervisor.py`
  - Do not wire first. Start as manual `/loop` or operator-triggered sidecar.
  - If wired later, it must be default disabled and route through the same candidate ingestion path.

## Candidate Schema

Prefer the existing sidecar schema:

```json
{
  "challenge_id": "123",
  "category": "crypto",
  "candidate": "flag{...}",
  "confidence": "high",
  "submit_recommendation": "never_direct_submit",
  "evidence_paths": [
    "artifacts/challenges/123/expert_notes.md"
  ],
  "notes": "Short explanation of how the flag was derived."
}
```

If multiple candidates are produced, write a JSON array and let the existing validator choose only valid high-confidence entries.

## Expert Prompt Template

Use a strict prompt like:

```text
You are solving one offline CTF challenge.

Hard constraints:
1. Use only the files provided from artifacts/challenges/<id>/.
2. Do not assume platform access.
3. Do not submit anything.
4. Do not guess. If evidence is insufficient, return no candidate.
5. A candidate must be backed by a concrete file path and derivation.
6. Prefer concise reproducible reasoning over speculation.

Output:
- expert_notes.md: evidence and derivation.
- expert_candidates.json: schema-compatible candidate if and only if a flag is supported.
```

## Implementation Plan

### Task 1: Mock-Only Expert Runner

Files:

- Create `ctf_agents/sidecar/openai_expert.py`
- Create `tests/test_openai_expert_sidecar.py`

Steps:

1. Write tests for disabled config returning no action.
2. Write tests for missing `OPENAI_API_KEY` returning a safe error.
3. Write tests that sandbox root is exactly `artifacts/challenges/<id>/`.
4. Implement `ExpertSidecarConfig`.
5. Implement `build_challenge_manifest(challenge_dir, config)`.
6. Implement mock `run_expert(..., mock_response=...)`.
7. Verify candidate output with `validate_codex_candidate`.

### Task 2: Dry Run Script

Files:

- Create `scripts/openai_expert_sidecar_dryrun.py`

Steps:

1. Create temp challenge directory under `artifacts/challenges/expert-demo`.
2. Write a tiny attachment.
3. Feed a mock response with one valid candidate.
4. Verify `expert_notes.md` and `expert_candidates.json` exist.
5. Verify logs/state/.secrets are untouched.

### Task 3: Config and Runbook

Files:

- Modify `configs/ai_contest.example.yaml`
- Create `runbooks/openai_expert_sidecar.md`

Steps:

1. Add default-disabled config block.
2. Document API key setup as `OPENAI_API_KEY=SET/UNSET`.
3. Document budget and call limits.
4. Document that expert output is advisory until validator + guard pass.

### Task 4: Optional Live API Probe

Only after mock tests pass:

1. Use a tiny public local challenge.
2. Call `gpt-5.5` first, not Pro.
3. Use a short timeout and one call.
4. Do not submit to any public platform.
5. If candidate is produced, route through validator and local GZCTF only.

## Acceptance Criteria

This work is accepted only if:

- Default config is disabled.
- All tests pass.
- No OpenAI key, webhook, cookie, or password is printed.
- Expert output cannot bypass validator.
- Expert output cannot bypass `FlagGuard`.
- No complete flags appear in `logs/`, `state/`, or `.secrets/`.
- A mock dry run produces a valid candidate.
- Live API is optional and not required for P0.

## Important Warning

Do not let this distract from the current priority:

```text
import public challenge bundles into local GZCTF
run supervisor download -> sidecar -> guard -> adapter -> Accepted/Wrong
```

Expert sidecar improves the ceiling on hard problems. It does not replace the platform rehearsal or the 5/9 real GZCTF P0 validation.
