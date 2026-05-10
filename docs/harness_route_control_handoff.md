# Harness Route Control Handoff

> For the next Codex: this is the implementation prompt for the AI identity harness route-control layer. Read this before making any code changes. Do not treat it as a design essay; treat it as the work order.

## Goal

Add a machine-readable route-control layer to the AI identity harness so the supervisor can:

- track `current_family` and `tried_families`
- classify route failures explicitly
- decide when to cut a route
- decide when to force public search
- decide when to ask for expert review
- decide when to hand work to a persistent background lane
- block `NO_CANDIDATE` until route exhaustion is actually proven

This must be done without changing the submit chain:

```text
supervisor -> validator -> FlagGuard -> adapter
```

## What Must Not Change

Do **not** redesign these parts:

- `ctf_agents/submit/gzctf_adapter.py`
- `ctf_agents/submit/flag_guard.py`
- `ctf_agents/sidecar/codex_validator.py`
- the sandbox / path boundary for Codex sidecar output
- the existing submit safety rules (freeze, rate limit, duplicate block, kill switch)

Do **not** move route logic into:

- `FlagGuard`
- the GZCTF adapter
- the Codex validator
- `ctf_agents/skill/router.py`

`ctf_agents/skill/router.py` is only coarse category routing for the skill workflow. It is not route control.

## Current Facts

The current harness already has these stable pieces:

- `scripts/ai_contest_supervisor.py` owns the deterministic contest loop.
- `ctf_agents/submit/state_store.py` owns submit safety, rate limits, and freeze state.
- `ctf_agents/submit/flag_guard.py` owns submit decisions.
- `ctf_agents/sidecar/codex_validator.py` owns Codex candidate schema/path validation.
- `runbooks/openai_expert_sidecar.md` and `runbooks/codex_sidecar.md` already define advisory-only sidecar behavior.

The missing layer is route control. Right now route choice still lives too much in prompt text and human interpretation.

## Required Architecture

Keep the submit chain intact. Add a separate route-control layer above it:

```text
challenge state
  -> route control decision
    -> agent / search / expert / persistent lane
      -> candidate evidence
        -> validator
          -> FlagGuard
            -> adapter
```

Recommended implementation boundary:

- create a pure route-control module for enums, dataclasses, and decision helpers
- keep `scripts/ai_contest_supervisor.py` as the glue and persistence owner
- keep submit safety in `FlagGuard` and `SubmissionStateStore`

Recommended new module:

- `ctf_agents/contest/route_control.py`

Recommended package scaffold:

- `ctf_agents/contest/__init__.py`

## Required State Schema

Add a `route_control` object under each challenge in `state/ai_contest_state.json`.

Minimum fields:

```json
{
  "route_control": {
    "schema_version": 1,
    "current_family": "crypto.lattice.multivariate_coppersmith",
    "tried_families": [],
    "failure_type": null,
    "failure_signals": [],
    "evidence_delta_score": 0,
    "same_family_no_delta_count": 0,
    "trivial_root_count": 0,
    "next_family": null,
    "route_decision": "continue_route",
    "pending_actions": [],
    "route_phase": "active",
    "route_cycle": 0,
    "route_budget_remaining": 0,
    "public_search_status": "not_required",
    "expert_review_status": "not_required",
    "persistent_lane_status": "not_started",
    "no_candidate_blockers": []
  },
  "route_control_action_state": {
    "public_search": {"status": "not_requested", "request_path": null, "requested_at": null},
    "expert_review": {"status": "not_requested", "request_path": null, "requested_at": null},
    "persistent_lane": {"status": "not_started", "request_path": null, "requested_at": null},
    "family_switch": {
      "status": "not_started",
      "from_family": null,
      "to_family": null,
      "switched_at": null
    }
  }
}
```

## Current Enums

```text
progress_type: challenge_progress | capability_progress | negative_progress | no_progress | platform_progress | documentation_progress
failure_type: parameteric_failure | structural_failure | tool_failure | representation_mismatch | evidence_insufficient | wrong_target | helper_bound_limit
route_decision: continue_route | cut_route | switch_family | spawn_public_search | spawn_expert_review | spawn_persistent_lane | block_no_candidate | allow_no_candidate
route_phase: active | cut | exhausted
public_search_status: not_required | required | running | complete | blocked_by_rules
expert_review_status: not_required | required | running | complete
persistent_lane_status: not_started | active | suspended | complete | stale | stopped
```

Recommended `tried_families` entry shape:

```json
{
  "route_id": "route_001",
  "family": "crypto.lattice.multivariate_coppersmith",
  "status": "active",
  "reason": "equations look like bounded small-root structure",
  "started_at_cycle": 1,
  "ended_at_cycle": null,
  "experiments": [],
  "failure_type": null,
  "failure_signals": [],
  "cut_reason": null
}
```

State persistence must survive restart. The next supervisor process must be able to answer:

- which family was active
- which family was already exhausted
- why it was cut
- whether search was already required
- whether expert review was already required
- whether route actions were already emitted or consumed
- whether `NO_CANDIDATE` is still blocked

## Route Decision Vocabulary

Use a small explicit decision set:

```text
continue_route
cut_route
switch_family
spawn_public_search
spawn_expert_review
spawn_persistent_lane
block_no_candidate
allow_no_candidate
```

Do not let the decision remain a prose note. The supervisor must be able to execute one of these actions directly.

## Progress Classification

Classify each experiment into one of:

```text
challenge_progress
capability_progress
negative_progress
no_progress
platform_progress
documentation_progress
```

Rules:

- `challenge_progress` means the current challenge was actually advanced.
- `negative_progress` means the current family was ruled out with evidence.
- `capability_progress` means a tool/helper/environment became usable.
- `platform_progress` means download / sync / setup work.
- `documentation_progress` means notes only.
- `no_progress` means nothing material happened.

Only `challenge_progress` and high-quality `negative_progress` may reset stall counters.

Never count helper smoke-test success as challenge progress.

## Failure Taxonomy

Use these failure types:

```text
parameteric_failure
structural_failure
tool_failure
representation_mismatch
evidence_insufficient
wrong_target
helper_bound_limit
```

Recommended precedence:

1. `wrong_target`
2. `structural_failure`
3. `representation_mismatch`
4. `helper_bound_limit`
5. `tool_failure`
6. `parameteric_failure`
7. `evidence_insufficient`

Important rule:

- do not downcast a stronger failure into a weaker one
- if helper smoke passes but the real instance is outside the helper's math coverage, call it `helper_bound_limit`

`helper_bound_limit` is the key new failure type for this harness.

## Route Cut Rules

Hard rules:

1. If the same family has 2 consecutive cycles without `challenge_progress` or qualified `negative_progress`, trigger a route review and block further same-family tuning.
2. If a small-root family produces repeated trivial roots on the real challenge, cut the route.
3. If the bound check is negative and the helper only produces trivial roots, cut the route immediately.
4. Helper smoke-test success never resets stall counters.
5. A route without explicit `continue_if` and `cut_if` conditions should not be started.

Recommended default:

- first trivial-root signal: record and confirm once
- second independent trivial-root signal: cut

## Public Search Gate

Public search must be a gate, not a suggestion.

Trigger it when:

- a route becomes structurally suspect
- `helper_bound_limit` is recorded
- the same family stalls twice
- a unique string / copied source / upstream hint appears
- `NO_CANDIDATE` is being considered

Search coverage should include:

- exact challenge name or unique strings
- source / upstream identifiers
- current family writeups
- helper implementations
- historical variants
- papers or docs when the algorithmic boundary is unclear

Every useful search result needs a disposition:

```text
adopt
vendor
single_challenge_use
reject_with_reason
queue_for_persistent_lane
send_to_expert_review
```

If the contest rule set forbids external network, replace public search with an approved offline corpus and record that clearly.

## Expert Review Gate

Use the optional OpenAI expert sidecar or equivalent as a route reviewer, not as a last-minute rescue path.

It should receive a route packet containing:

- challenge summary
- current family
- tried families
- experiments
- evidence delta
- failure signals
- public search summary

It should return structured output with:

- `verdict`
- `failure_class`
- `continue_current_family`
- `next_families`
- `first_experiment`
- `stop_condition`
- `no_candidate_blockers`

If the expert suggests a next experiment, that experiment must either be executed or handed to the persistent lane. It must not be ignored.

## Persistent Lane

The persistent lane is a background investigation lane.

Its job is to keep difficult challenges alive without blocking the main scheduler.

Track at least:

- open questions
- alternative families
- public search ledger
- helper evaluation
- negative evidence
- `no_candidate_blockers`

Use explicit statuses:

```text
route_phase: active | cut | exhausted
public_search_status: not_required | required | running | complete | blocked_by_rules
expert_review_status: not_required | required | running | complete
persistent_lane_status: not_started | active | suspended | complete | stale | stopped
```

While the persistent lane is active and blockers remain, `NO_CANDIDATE` is not allowed.

## Packet Emission and Result Consumption

Packet emission is tracked by supervisor state. The supervisor writes request
packets and advances `route_control_action_state` when it emits a public-search,
expert-review, or persistent-lane action.

The supervisor writes request packets and advances `route_control_action_state` when it emits a route action.

Result consumption is a separate contract from packet emission. The file names
and schemas below are the handoff shape for a downstream consumer that folds
search, review, or lane output back into state.

Currently emitted request packets:

```text
public_search_request.json
expert_review_packet.json
persistent_lane_request.json
```

Expected result files:

```text
public_search_result.json
public_search_ledger.json
expert_review_result.json
persistent_lane_update.json
persistent_lane_stop_report.json
```

Use these result schemas:

- `public_search_result.json` or `public_search_ledger.json`: `status`,
  `coverage`, `results`, optional `next_family`, and `no_candidate_blockers`.
  Each result entry should include `query`, `url` or `source`, `summary`, and
  `disposition`.
- `expert_review_result.json`: `verdict`, `failure_class`,
  `continue_current_family`, `next_families`, `first_experiment`,
  `stop_condition`, and `no_candidate_blockers`.
- `persistent_lane_update.json`: `status`, `open_questions`,
  `alternative_families`, `public_search_ledger`, `helper_evaluation`,
  `negative_evidence`, and `no_candidate_blockers`.
- `persistent_lane_stop_report.json`: `status`, `stop_reason`,
  `exhausted_families`, `remaining_blockers`, and `no_candidate_allowed`.

`codex_sidecar.allow_submit` and `codex_sidecar.allow_secret_read` are audit defaults, not route-control enforcement knobs.

## NO_CANDIDATE Standard

`NO_CANDIDATE` is a certificate, not a mood.

It may only happen after:

- local baseline attempt
- short Codex lane
- at least one explicit family
- at least one family switch or a justified impossibility
- failure classification
- public search or an approved offline substitute
- expert review
- persistent lane completion or suspension without blockers
- helper gaps classified
- candidate queue empty

If any high-value blocker remains, `NO_CANDIDATE` is blocked.

## File Responsibilities

### 1. Pure route-control module

Create `ctf_agents/contest/route_control.py` with:

- enums for route decisions, phases, statuses, and failure types
- dataclasses for route state, family ledger entries, search ledger entries, expert review packets, and persistent lane state
- pure decision helpers:
  - `classify_progress(...)`
  - `classify_failure(...)`
  - `should_trigger_public_search(...)`
  - `should_trigger_expert_review(...)`
  - `should_cut_route(...)`
  - `choose_next_family(...)`
  - `can_emit_no_candidate(...)`

Keep it pure. No platform I/O. No direct reading of secrets. No submit logic.

### 2. Supervisor glue

Modify `scripts/ai_contest_supervisor.py` to:

- create the new `route_control` sub-object when a challenge is first seen
- load and save route-control state with the rest of the challenge state
- update route state each cycle
- record route decisions in the JSONL log
- gate public search / expert review / persistent lane / no_candidate on state, not prompt text
- keep the current submit path unchanged

Do not make `FlagGuard` or the adapter aware of route families.

### 3. Documentation sync

Update the prompt/runbook docs so they match the real state machine:

- `docs/solve_first_loop_policy.md`
- `docs/loop_prompt_solve_first.md`
- `runbooks/ai_identity.md`
- `runbooks/contest_day_ai_identity.md`

If the route-control layer is implemented, the loop prompt must say that helper success alone is not challenge progress and that `NO_CANDIDATE` requires route exhaustion evidence.

## Test Plan

Add tests for route control itself and for supervisor integration.

Recommended new test file:

- `tests/test_route_control.py`

Recommended supervisor test additions:

- `tests/test_ai_contest_supervisor.py`

Minimum behaviors to cover:

1. Route state initializes with sane defaults.
2. Route state round-trips through restart.
3. Capability progress does not reset stall counters.
4. `helper_bound_limit` cuts the route.
5. Trivial-root repetition increments the right counters and triggers a cut.
6. Public search becomes required after structural failure.
7. Expert review is required before `NO_CANDIDATE`.
8. Persistent lane blockers prevent premature `NO_CANDIDATE`.
9. Route decisions do not bypass the existing submit chain.

## Recommended Rollout Order

1. Add the pure route-control module.
2. Extend supervisor challenge state with `route_control`.
3. Wire route decisions and gate triggers into the supervisor loop.
4. Add public search / expert review / persistent lane state recording.
5. Add `NO_CANDIDATE` exhaustion checks.
6. Update docs and prompts to match the real behavior.
7. Add and run tests.

## Ready-to-Paste Prompt for the Next Codex

Use this as the first instruction to the implementation agent:

```text
You are implementing the route-control layer for the DLUT AI identity harness in /home/wuwai/dlut-ctf-2026. Read docs/harness_route_control_handoff.md first, then implement the route-control state machine exactly as described.

Hard constraints:
1. Do not redesign the submit chain. Keep supervisor -> validator -> FlagGuard -> adapter unchanged.
2. Do not move route logic into FlagGuard, the adapter, or the Codex validator.
3. Do not make helper smoke-test success count as challenge progress.
4. Do not let public search, expert review, persistent lane, or NO_CANDIDATE remain implicit in prompt text. They must become stateful gates.
5. Do not allow NO_CANDIDATE until route exhaustion is proven by machine-readable state.

Implement in this order:
1. Add a pure route-control module with enums, dataclasses, and decision helpers.
2. Extend scripts/ai_contest_supervisor.py with route_control state under each challenge.
3. Wire in failure classification, public search gating, expert review gating, persistent lane status, and NO_CANDIDATE exhaustion checks.
4. Update docs/solve_first_loop_policy.md, docs/loop_prompt_solve_first.md, runbooks/ai_identity.md, and runbooks/contest_day_ai_identity.md so they match the new behavior.
5. Add tests for route-control logic and supervisor integration.

Files to touch:
- create ctf_agents/contest/route_control.py
- create ctf_agents/contest/__init__.py
- modify scripts/ai_contest_supervisor.py
- modify tests/test_ai_contest_supervisor.py
- create tests/test_route_control.py
- modify docs/solve_first_loop_policy.md
- modify docs/loop_prompt_solve_first.md
- modify runbooks/ai_identity.md
- modify runbooks/contest_day_ai_identity.md

Acceptance criteria:
- route-control state survives restart
- helper_bound_limit cuts a route
- repeated trivial roots cut a route
- capability_progress does not reset stall counters
- public search becomes required after structural failure
- expert review is required before NO_CANDIDATE
- persistent lane blockers prevent premature NO_CANDIDATE
- the submit path remains unchanged
```

## Notes for the Implementer

- Keep the route-control layer deterministic.
- Prefer pure functions and small state helpers.
- If a new schema version is needed, preserve backward compatibility for old state files.
- Do not expand `state_store.py` beyond submit safety.
- Do not add platform I/O to the new route-control module.
- Do not rely on chat memory; the state file must carry the route history.
