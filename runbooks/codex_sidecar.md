# Codex Sidecar Runbook

`codex-plugin-cc` lets Claude Code invoke OpenAI Codex as a sidecar
agent for **per-challenge exploration / review only**.  Codex is P2:
useful but optional.  If the plugin fails to load, the supervisor must
keep running on its own.

## Hard rules (never relaxed)

The supervisor will **drop** any Codex output that violates these:

```text
1. Codex must NEVER read .env or .secrets/.
2. Codex must NEVER mutate state/ai_contest_state.json or
   logs/submission_state.json.
3. Codex must NEVER call GZCTF (no curl, no requests, no browser).
4. Codex must NEVER submit a flag.  Submission is FlagGuard's job.
5. Codex output paths must live under
   artifacts/challenges/<id>/  — anything else is rejected.
6. Codex must mark every candidate
   submit_recommendation = "never_direct_submit".
```

These are enforced by ``ctf_agents/sidecar/codex_validator.py``.  The
dry-run in ``scripts/codex_sidecar_dryrun.py`` proves all five
negative scenarios are caught:

```bash
python scripts/codex_sidecar_dryrun.py
# → ALL CHECKS PASSED
```

## What Codex IS allowed to do

```text
- Inspect artifacts/challenges/<id>/ contents
- Run local non-platform tools on a copy of the attachment
  (binwalk, strings, exiftool, zsteg, foremost, hexdump, …)
- Write artifacts/challenges/<id>/codex_notes.md
- Write artifacts/challenges/<id>/codex_candidates.json
- Write artifacts/challenges/<id>/patches/*.patch
  (small local helper script tweaks; never touching secrets/state)
- Diagnose logs/*.jsonl after a supervisor error
```

## Output schema

`artifacts/challenges/<id>/codex_candidates.json` is a JSON array of:

```json
{
  "challenge_id": "123",
  "candidate": "flag{...}",
  "confidence": "low | medium | high",
  "evidence_paths": [
    "artifacts/challenges/123/evidence/strings.txt",
    "artifacts/challenges/123/evidence/binwalk_extract/img.png"
  ],
  "submit_recommendation": "never_direct_submit",
  "notes": "short rationale; multi-line ok"
}
```

Forbidden top-level keys (cause the candidate to be dropped silently
with a `codex_candidate_rejected` log event):

```text
submit, platform_call, secret, cookie, password, api_key,
token, force_submit, bypass_guard, auth
```

`codex_notes.md` has no schema; it's prose for the operator to read
post-game.  Do not put credentials, raw flags, or non-current-challenge
data in it.

## Decision flow

```
                ┌────────────────────────────────────┐
                │  Codex (codex-plugin-cc)           │
                │   • inspect artifact dir           │
                │   • run local tools                │
                └──────────────┬─────────────────────┘
                               │ writes
                               ▼
            artifacts/challenges/<id>/codex_candidates.json
                               │
                               ▼  validate
            ctf_agents/sidecar/codex_validator
                               │
                               ▼  if PASS
                    FlagCandidate   ←  built by supervisor
                               │
                               ▼
                          FlagGuard.decide()
                               │
                  AUTO_SUBMIT  │   HUMAN_REVIEW / HOLD / REJECT
                               ▼
                       GZCTFAdapter.submit_flag_for_game()
                               │
                               ▼
                        platform answer
```

`Codex output → adapter` (skipping FlagCandidate / FlagGuard) is
disallowed at every level — by validator, by supervisor design, and
by the runbook contract.

## Plugin install + verification

`codex-plugin-cc` is a Claude Code plugin.  Install / verify before
contest day, not during.

### Install

Per the upstream README (``https://github.com/openai/codex-plugin-cc``)
the plugin is **not on npm**.  It's a Claude Code marketplace plugin.
From inside Claude Code:

```text
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/reload-plugins
/codex:setup
```

Requirements (also from upstream README):

- ChatGPT subscription (any tier) **or** OpenAI API key
- Node.js ≥ 18.18

The Codex CLI itself (``codex``) is a separate package; ``/codex:setup``
will offer to ``npm install -g @openai/codex`` if it's missing, or you
can do it yourself ahead of time:

```bash
npm install -g @openai/codex
codex login           # one-time
```

This repo's WSL already has the Codex CLI on PATH (verified
2026-05-09): ``which codex`` → ``~/.nvm/.../bin/codex``.  What's still
needed for sidecar mode is the **Claude Code plugin** that delegates to
Codex via slash commands.

If the plugin install fails or Claude Code is not the orchestration
shell:

```yaml
# configs/ai_contest.yaml
codex_sidecar:
  enabled: false
```

The supervisor runs fine without Codex by design.

### What the plugin actually exposes

Slash commands inside Claude Code (not a separate CLI):

```text
/codex:review              normal read-only review of current work
/codex:adversarial-review  steerable challenge review
/codex:rescue              delegate a background task to Codex
/codex:status              check background job state
/codex:result              fetch the finished output
/codex:cancel              kill a running job
```

For the AI-identity contest sidecar use case, the relevant flow is
``/codex:rescue`` with a prompt that pins the artifact path + schema:

```text
/codex:rescue --background
> Read artifacts/challenges/<id>/. Run binwalk/strings/exiftool on the
> attached files (do NOT touch .env, .secrets/, state/, or call any
> network endpoint).  Write findings to
> artifacts/challenges/<id>/codex_notes.md and a candidate (if any) to
> artifacts/challenges/<id>/codex_candidates.json conforming to the
> schema in runbooks/codex_sidecar.md.  Set submit_recommendation =
> "never_direct_submit".  Do not submit.
```

Then later:

```text
/codex:status
/codex:result
```

The output lands at the artifact path the prompt specified.  The
supervisor's normal challenge tick will discover and validate it.

### Verify after install

From inside Claude Code (after ``/reload-plugins``):

```text
/plugin              # list installed plugins; expect codex@openai-codex
/codex:setup         # confirms Codex CLI + auth status
```

From a fresh shell (sandbox + dry-run; does not need the plugin loaded):

```bash
# 1. codex CLI on PATH
which codex || echo "codex CLI missing — run npm install -g @openai/codex"

# 2. Sandbox + validator contract
source tools/env.sh
python scripts/codex_sidecar_dryrun.py
# expect: ALL CHECKS PASSED
```

The dry-run does NOT call the real Codex plugin — it only proves the
validator + sandbox contract.  After the dry-run is green, do one real
``/codex:rescue`` against the demo artifact:

```text
# Inside Claude Code with the plugin loaded:
/codex:rescue --background
> review artifacts/challenges/demo-001/ and write a candidate per the
> sidecar runbook schema; do not submit
```

Then re-run the dry-run validator (``python scripts/codex_sidecar_dryrun.py``)
to confirm Codex's actual output passes.  If validation fails, drop
the candidate and read ``logs/ai-contest-*.jsonl`` for the
``codex_candidate_rejected`` event.

## Config stanza

`configs/ai_contest.yaml` may include:

```yaml
codex_sidecar:
  enabled: false              # default off; flip after 5/9 plugin verify
  max_parallel_tasks: 1       # one challenge at a time
  timeout_s: 600              # kill Codex if it loops
  allow_patch: true           # local helper script edits OK
  allow_submit: false         # MUST stay false; flag submit only via guard
  allow_secret_read: false    # MUST stay false
  artifact_root: "artifacts/challenges"
```

`allow_submit` and `allow_secret_read` are **read-only constants** in
spirit — the validator and supervisor never honour `true` for either.
They live in config purely so the operator can audit "yes, this knob
is wired the safe way" before kickoff.

## Failure modes

| Symptom | Cause | What supervisor does |
|---|---|---|
| `codex_candidate_rejected` in logs/ai-contest-*.jsonl | Codex output failed validator (forbidden key, bad path, bad confidence, missing key) | Drop candidate; supervisor continues with non-sidecar agents |
| `codex-plugin-cc` not installed | install skipped or failed | Set `codex_sidecar.enabled: false`; supervisor never invokes Codex |
| Codex hangs > timeout_s | LLM stuck / network blip | Supervisor cancels (it's a sidecar with a hard timeout); next tick proceeds |
| Codex writes outside `artifacts/challenges/<id>/` | misconfiguration / prompt drift | Validator rejects ALL candidates from that file; operator inspects manually |
| Codex tries to write `state/` or `.secrets/` | prompt drift / hostile prompt | filesystem permissions + validator path check both refuse |

## 5/9 dress-rehearsal checklist

After the real GZCTF URL is configured + healthcheck passes:

- [ ] `claude code plugins list | grep codex` shows the plugin
- [ ] `python scripts/codex_sidecar_dryrun.py` → ALL CHECKS PASSED
- [ ] Real Codex invocation against `artifacts/challenges/demo-001/`
      produces a fresh `codex_candidates.json`
- [ ] Validator passes that fresh file (or rejects it with a clear
      reason)
- [ ] Supervisor JSONL contains the expected
      `codex_candidate_received` / `codex_candidate_rejected` event
      depending on the validator outcome
- [ ] No platform call originated from the Codex sidecar in the log
- [ ] `.secrets/` / `.env` / `state/ai_contest_state.json` modification
      times unchanged after the Codex run

If any item fails, set `codex_sidecar.enabled: false` and proceed
without the sidecar.  Codex is P2 — its absence does not block 5/10.
