# AI Identity Runbook

Ops doc for the **AI identity** track of the DLUT 5/10 skill contest.
The current contest-day AI identity source of truth is
`runbooks/contest_day_ai_identity.md`. The solve-first prompt, compact loop
contract, and loop policy live in `docs/loop_prompt_solve_first.md`,
`docs/opus_loop_contract.md`, and `docs/solve_first_loop_policy.md`.

Boundary:

```text
Knowledge contest: may be handled by humans / separate lookup tooling.
Skill contest: main battlefield for AI identity, supervisor, guard, and solve-first loop.
```

## Track Constraints

From the organizer's rules screenshot:

- 10 minutes after start to prepare (download attachments, deploy AI).
- After t+10min, **no human operations**.
- Whole contest, **only one prompt** to the Agent.
- Agent tools allowed.

Operational implication: build a **single-prompt autonomous loop**
that, after the prompt, requires zero human input until contest end.

There is no human reviewer in this track, so the contest profile sets
every category to `auto_submit` and drops the score thresholds to 0.0.
A valid candidate is never demoted to `HUMAN_REVIEW`.  Only hard gates
(format / duplicate / rate-limit / freeze / kill-switch /
CheatDetected) can stop a submission.  See `runbooks/guard_policy.md`
for the full table.

## System Boundaries

```
Claude Code  → reads runbooks, kicks off supervisor, watches logs
   │
   ▼
ai_contest_supervisor.py  (deterministic state machine — sole driver)
   │
   ├─→ GZCTFAdapter   (only platform I/O; login, submit, status, attach)
   ├─→ FlagGuard      (only submit gate; format, score, freeze, rate-limit)
   ├─→ misc_real      (per-category solving agent; no submit power)
   └─→ JSONL state    (only source of runtime truth)
```

Hard rules (mirrored in code, never bypass):

1. Guard is the only submit gate.
2. Adapter is the only platform I/O.
3. State / log files are the only source of truth.
4. LLM guesses are never flags.
5. Codex outputs are never directly submitted.
6. Route control is persisted under each challenge in supervisor state; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate.
7. Helper smoke success is `capability_progress`, not `challenge_progress`.

## The Single Prompt

Use the prompt in `docs/loop_prompt_solve_first.md`. It is the current
solve-first startup prompt and recurring `/loop` instruction. Each loop tick
also rereads the compact contract in `docs/opus_loop_contract.md`.

Reference text:

```text
You are the only autonomous AI agent for the DLUT skill contest AI identity. First read `runbooks/contest_day_ai_identity.md`, `docs/loop_prompt_solve_first.md`, `docs/opus_loop_contract.md`, `docs/solve_first_loop_policy.md`, and `configs/ai_contest.yaml`. Start or verify `scripts/ai_contest_supervisor.py --config configs/ai_contest.yaml`, keep solving in solve-first mode, and let supervisor -> validator -> FlagGuard -> GZCTFAdapter own every platform operation and flag submission.
```

## 10-Minute Prep Plan (t+0 to t+10)

| Window | Action |
|---|---|
| 0:00 – 1:00 | Activate venv: `source tools/env.sh`. Open `runbooks/contest_day_ai_identity.md`. |
| 1:00 – 2:30 | Fill `configs/ai_contest.yaml` from `configs/ai_contest.example.yaml`: real `gzctf.base_url`, `game_id` (from `/api/game`), and verify `.env` has `GZCTF_USERNAME` / `GZCTF_PASSWORD`. |
| 2:30 – 4:00 | `python scripts/ai_contest_supervisor.py --healthcheck-only` — must print `[PASS] healthcheck`. |
| 4:00 – 5:30 | If detail/attachment endpoint is reachable: pick one challenge, run `python -c "from ctf_agents.submit.gzctf_adapter import GZCTFAdapter; ..."` to download one attachment via the adapter so cookies + path + scope all confirmed. |
| 5:30 – 6:30 | Submit/status healthcheck on the organizer's test challenge if one is provided.  Confirm `submit_payload_mode` matches (plaintext vs encrypted). |
| 6:30 – 7:30 | Go/No-Go: if any skill-contest P0 item from the gate failed, use the non-AI fallback; otherwise continue. |
| 7:30 – 8:30 | Open Claude Code, paste the solve-first prompt from `docs/loop_prompt_solve_first.md`. |
| 8:30 – 10:00 | Watch the heartbeat in `logs/ai-contest-*.jsonl`. After 10:00, no human operations. |

## 5/9 Go / No-Go Gate

The supervisor + adapter + rehearsal pass these on the real GZCTF test
environment:

- [ ] login/profile/team/game/challenge reachable
- [ ] attachment downloads via adapter session
- [ ] submit returns submitId
- [ ] status polls to terminal
- [ ] `submit_payload_mode` known (plaintext / encrypted)
- [ ] supervisor starts with one prompt/command
- [ ] guard is the only submit path (verified by reading code path)
- [ ] duplicate flag blocked locally (rehearsal scenario)
- [ ] WrongAnswer freezes after one attempt
- [ ] Accepted stops that challenge
- [ ] rate limit honored
- [ ] state/log/submissions/heartbeat all written
- [ ] unattended run lasts ≥ 30 min on test env

If any skill-contest item fails -> use the non-AI fallback for 5/10.

## Sub-runbooks

- `runbooks/gzctf_adapter.md` — adapter API, payload modes, attachment, polling
- `runbooks/guard_policy.md` — auto/human/hold/freeze rules under AI identity
- `runbooks/failure_modes.md` — what to do on adapter, scope, hash-gate, freeze, restart errors
- `runbooks/contest_day_ai_identity.md` — minute-by-minute on 5/10
