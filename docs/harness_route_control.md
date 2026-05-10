# Harness Route Control

This note records the current route-control judgment for the AI identity harness.
It is separate from the submit chain. The submit chain stays:
`supervisor -> validator -> FlagGuard -> adapter`.

## What Is Already Implemented

The project already has the following working pieces:

- `ctf_agents/submit/gzctf_adapter.py`
- `scripts/ai_contest_supervisor.py`
- `ctf_agents/submit/flag_guard.py`
- `ctf_agents/sidecar/codex_validator.py`
- `runbooks/ai_identity.md`
- `runbooks/contest_day_ai_identity.md`
- `runbooks/gzctf_adapter.md`
- `runbooks/guard_policy.md`
- `runbooks/failure_modes.md`
- `runbooks/codex_sidecar.md`
- `runbooks/openai_expert_sidecar.md`
- `scripts/runtime_preflight.py`
- `scripts/codex_sidecar_dryrun.py`
- `scripts/openai_expert_sidecar_dryrun.py`
- `tools/ctf_helpers/crypto/multivariate_coppersmith.sage`

These are not the open problems. The open problem is route control:

- whether a challenge is still worth the current family
- when to cut a route
- when to force public search
- when to ask the configured expert sidecar for review
- when to hand a challenge to a persistent Codex lane
- when `NO_CANDIDATE` is actually earned

## Core Diagnosis

The main weakness is not submission safety. It is route control.

The harness still tends to treat the first plausible route as the default
for too long. That makes it easy to confuse:

- helper success with challenge progress
- parameter sweeps with progress
- local execution success with route validity
- logs becoming more detailed with actual evidence gain

The system therefore needs explicit route-state, not just prompt text.

## Required Route-State

Each challenge needs route-state fields that are machine-readable and
auditable.

Minimum fields:

- `current_family`
- `tried_families`
- `failure_type`
- `failure_signals`
- `evidence_delta_score`
- `same_family_no_delta_count`
- `trivial_root_count`
- `next_family`
- `route_decision`
- `no_candidate_blockers`

Recommended additional fields:

- `route_phase`
- `route_cycle`
- `last_meaningful_progress_cycle`
- `route_budget_remaining`
- `public_search_status`
- `expert_review_status`
- `persistent_lane_status`

## Progress Classification

The harness should distinguish these outcomes:

- `challenge_progress`
- `capability_progress`
- `negative_progress`
- `no_progress`
- `platform_progress`
- `documentation_progress`

Only `challenge_progress` and high-quality `negative_progress` should
reset route-stall counters.

Examples:

- helper smoke test passed -> `capability_progress`
- attachment downloaded -> `platform_progress`
- route ruled out with evidence -> `negative_progress`
- non-trivial relation found -> `challenge_progress`

Helper success alone is never challenge progress.

## Failure Taxonomy

Recommended failure types:

- `parameteric_failure`
- `structural_failure`
- `tool_failure`
- `representation_mismatch`
- `evidence_insufficient`
- `wrong_target`
- `helper_bound_limit`

Short meanings:

- `parameteric_failure`: family may still fit, but parameters or bounds are wrong.
- `structural_failure`: family assumptions are broken; cut route.
- `tool_failure`: helper or runtime missing, but family may still fit.
- `representation_mismatch`: the current representation does not match the family.
- `evidence_insufficient`: not enough artifact evidence yet.
- `wrong_target`: working on the wrong artifact or object.
- `helper_bound_limit`: helper works, but the real instance is outside its coverage.

`helper_bound_limit` is the important one for this project. It prevents the
common mistake of treating a working helper as proof that the current family
still deserves more time.

## Route-Cut Rules

Hard rules:

1. Two consecutive cycles in the same family without challenge evidence delta
   must trigger a route review.
2. Repeated trivial roots in a small-root family must trigger a cut.
3. A negative bound certificate must trigger a cut.
4. Helper smoke success does not reset stall counters.
5. `helper_bound_limit` means cut route, not more tuning.
6. A route without explicit `continue_if` and `cut_if` conditions should not be
   started.

Recommended default behavior:

- first trivial-root signal: record and confirm once
- second independent trivial-root signal: cut
- structural failure with negative bound: cut immediately

## Public Search Gate

Public search must be a gate, not a suggestion.

Trigger it when:

- a route becomes structurally suspect
- `helper_bound_limit` is recorded
- the same family stalls twice
- a unique string / copied source / library hint appears
- `NO_CANDIDATE` is being considered

Search should cover:

- exact challenge / title / unique strings when available
- source or upstream identifiers
- current family writeups
- helper implementations
- historical variants
- papers or docs when the algorithmic boundary is unclear

Every useful result needs a disposition:

- `adopt`
- `vendor`
- `single_challenge_use`
- `reject_with_reason`
- `queue_for_persistent_lane`
- `send_to_expert_review`

## Expert Review Gate

The configured expert sidecar should be used as a route reviewer, not as an
afterthought.  The example config currently keeps this sidecar disabled by
default and names `gpt-5.2` / `gpt-5.2-pro` as the default model pair.

It should receive a route packet that includes:

- challenge summary
- current family
- tried families
- experiments
- evidence delta
- failure signals
- public search summary

It should return structured output with:

- verdict
- failure_class
- continue_current_family
- next_families
- first_experiment
- stop_condition
- no_candidate_blockers

If the expert suggests a next experiment, that experiment should either be
executed or handed to a persistent lane. It should not be ignored.

## Persistent Codex Lane

The persistent lane is a background investigation lane.

Its job is to keep difficult challenges alive without blocking the main
scheduler.

It should maintain:

- alternative families
- public search ledger
- helper evaluation
- negative evidence
- `no_candidate_blockers`

It should stop only when:

- the challenge is accepted
- the challenge is frozen due to wrong answer
- the challenge is globally stopped
- all blockers are exhausted and a stop report exists

While the persistent lane is active and blockers remain, `NO_CANDIDATE` is not
allowed.

## Helper Capability Policy

Helpers should be promoted to shared capability only when they:

- have a documented source and license
- pass positive toy tests
- pass negative or boundary tests
- have a structured output contract
- do not require forbidden I/O
- are reusable across challenges

Toy success is not challenge success.

If a helper works on a toy case but the real challenge gives only trivial roots
or a negative bound certificate, record `helper_bound_limit` and cut the route.

## NO_CANDIDATE Standard

`NO_CANDIDATE` is a certificate, not a mood.

It should only happen after:

- local baseline attempt
- short Codex lane
- at least one explicit family
- at least one family switch or a justified impossibility
- failure classification
- public search or an approved offline substitute
- configured expert-sidecar route review
- persistent lane completion or suspension without blockers
- helper gaps classified
- candidate queue empty

If any high-value blocker remains, `NO_CANDIDATE` is blocked.

## What Not To Change

Do not redesign:

- the supervisor / validator / FlagGuard / adapter chain
- the adapter's platform-I/O boundary
- the Codex sandbox contract
- the default disablement of the OpenAI expert sidecar

The work is route control, not submit-chain redesign.

## Immediate Next Step

The next implementation step is to make the route-control state explicit in the
supervisor state schema and the loop policy, then wire the cut/search/expert/
persistent/no_candidate gates to that state.

## Implementation-Ready Gap Analysis

This section is the part that turns the analysis above into something the
current codebase can actually implement and test. The earlier sections explain
what is wrong in principle. This section explains what is still missing in
mechanical terms.

### 1. Separate `category` from `current_family`

The current supervisor already uses `category` as a coarse dispatch label for
agent selection and auto-submit policy. That is not route control.

`current_family` must be a separate field with a different meaning:

- `category` answers: which broad bucket is this challenge in?
- `current_family` answers: which attack hypothesis are we currently testing?

A challenge can stay in the same category while switching families several
times. For example:

- `category = crypto`
- `current_family = crypto.lattice.multivariate_coppersmith`
- later `current_family = crypto.algebraic.elimination`

That is a route switch, even though the category did not change.

If the implementation mixes these two ideas, the supervisor will keep confusing
“same broad task” with “same route”, which is exactly the error this design is
trying to remove.

### 2. Route state must live in the supervisor state file

The route controller needs a persistent, machine-readable state object under
each challenge in `state/ai_contest_state.json`. The route state cannot live
only in prompt text, logs, or sidecar notes.

Recommended shape:

```json
{
  "route_control": {
    "schema_version": 1,
    "current_family": "crypto.lattice.multivariate_coppersmith",
    "tried_families": [
      {
        "family": "crypto.lattice.univariate_coppersmith",
        "status": "exhausted",
        "route_id": "route_001",
        "started_at_cycle": 0,
        "ended_at_cycle": 1,
        "failure_type": "representation_mismatch",
        "failure_signals": ["no_univariate_form", "bounds_not_satisfied"]
      }
    ],
    "failure_type": "helper_bound_limit",
    "failure_signals": ["trivial_roots_only", "bound_certificate_negative"],
    "evidence_delta_score": 0,
    "same_family_no_delta_count": 2,
    "trivial_root_count": 2,
    "next_family": "crypto.algebraic.elimination",
    "route_decision": "cut_route",
    "pending_actions": ["cut_route", "spawn_public_search"],
    "route_phase": "cut",
    "route_cycle": 2,
    "route_budget_remaining": 0,
    "public_search_status": "required",
    "expert_review_status": "not_required",
    "persistent_lane_status": "active",
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

If this information is not persisted, the next loop cycle will forget why a
route was cut and will drift back into the same family.

### 3. Make the counters have precise reset rules

Three counters matter most:

- `evidence_delta_score`
- `same_family_no_delta_count`
- `trivial_root_count`

Their semantics need to be explicit.

Recommended rules:

- `same_family_no_delta_count` increments on any cycle without
  `challenge_progress` or qualified `negative_progress`.
- It resets only when a route produces real evidence gain.
- `capability_progress` does not reset it.
- `documentation_progress` does not reset it.
- `trivial_root_count` increments only when the same route produces a real
  trivial-root outcome on the real challenge, not on a toy helper test.
- `evidence_delta_score` should be bounded and monotonic enough to audit, not a
  free-form vibe metric.

This is the place where most “it feels like progress” mistakes must be removed.

### 4. The failure taxonomy needs precedence, not just labels

The doc already lists the right failure types. What it still lacks is
classification precedence.

Recommended ordering:

1. `wrong_target`
2. `structural_failure`
3. `representation_mismatch`
4. `helper_bound_limit`
5. `tool_failure`
6. `parameteric_failure`
7. `evidence_insufficient`

The reason for precedence is simple: some failures are stronger than others.
If a helper works on toy cases but the real instance stays outside its math
boundaries, that is not just `parameteric_failure`; it is
`helper_bound_limit`.

Without precedence, the system will keep relabeling the same failure with softer
names and never actually cut the route.

### 5. Define the gate statuses as real enums

The following status fields should not stay as prose:

- `public_search_status`
- `expert_review_status`
- `persistent_lane_status`
- `route_phase`

The implemented enums are:

```text
public_search_status: not_required | required | running | complete | blocked_by_rules
expert_review_status: not_required | required | running | complete
persistent_lane_status: not_started | active | suspended | complete | stale | stopped
route_phase: active | cut | exhausted
```

The current route-control enums are:

```text
progress_type: challenge_progress | capability_progress | negative_progress | no_progress | platform_progress | documentation_progress
failure_type: parameteric_failure | structural_failure | tool_failure | representation_mismatch | evidence_insufficient | wrong_target | helper_bound_limit
route_decision: continue_route | cut_route | switch_family | spawn_public_search | spawn_expert_review | spawn_persistent_lane | block_no_candidate | allow_no_candidate
```

That makes the state inspectable by code, tests, and future operators.

### 5a. Packet emission versus enforced gate

Packet emission is tracked by supervisor state. When a route decision asks for
public search, expert review, or a persistent lane, the supervisor writes a
request packet under `artifacts/challenges/<id>/...`, moves the matching
`route_control_action_state` entry out of its initial state, and removes the
emitted action from `pending_actions` so restart does not duplicate the same
request.

The supervisor writes request packets and advances `route_control_action_state` when it emits a route action.

Result consumption is a separate contract from packet emission. The file names
and schemas below are the handoff shape for a downstream consumer that folds
search, review, or lane output back into state.

The currently emitted request packet names are:

- `public_search_request.json`
- `expert_review_packet.json`
- `persistent_lane_request.json`

Result files should use these names and schemas so a consumer can update
`route_control`, `route_control_action_state`, and `persistent_lane` without
relying on prose:

- `public_search_result.json` or `public_search_ledger.json`
  - `status`: `complete` or `blocked_by_rules`
  - `coverage`: list of searched titles, strings, sources, helpers, writeups, or papers
  - `results`: list of objects with `query`, `url` or `source`, `summary`, and `disposition`
  - `next_family`: optional family suggested by the search
  - `no_candidate_blockers`: remaining blockers
- `expert_review_result.json`
  - `verdict`
  - `failure_class`
  - `continue_current_family`
  - `next_families`
  - `first_experiment`
  - `stop_condition`
  - `no_candidate_blockers`
- `persistent_lane_update.json`
  - `status`: `active`, `suspended`, `complete`, `stale`, or `stopped`
  - `open_questions`
  - `alternative_families`
  - `public_search_ledger`
  - `helper_evaluation`
  - `negative_evidence`
  - `no_candidate_blockers`
- `persistent_lane_stop_report.json`
  - `status`: `complete`, `suspended`, or `stopped`
  - `stop_reason`
  - `exhausted_families`
  - `remaining_blockers`
  - `no_candidate_allowed`

`codex_sidecar.allow_submit` and `codex_sidecar.allow_secret_read` are audit defaults, not route-control enforcement knobs.

### 6. Gate conditions need exact triggers and exact exits

Right now the doc says “must search”, “must review”, and “must hang a background
lane”. Those are correct, but still too vague for implementation.

Each gate should have:

- a trigger
- a required input packet
- an output schema
- an exit condition

Examples:

#### Public search gate

Trigger:

- structural failure
- `helper_bound_limit`
- `same_family_no_delta_count >= 2`
- unique string / copied source / upstream hint
- `NO_CANDIDATE` considered

Exit:

- all useful results have a disposition
- search coverage is complete for the current question
- the supervisor can justify either a cut or a continuation

#### Expert review gate

Trigger:

- first confirmed structural failure
- post-search uncertainty
- two failed specific routes in the same major family
- `NO_CANDIDATE` path approaching

Exit:

- expert output is structured
- the next family or stop condition is explicit
- the result is recorded into route state, not only into prose

#### Persistent lane gate

Trigger:

- useful but unresolved next family
- search results worth follow-up
- expert proposed next experiment that is not yet run

Exit:

- lane either produces a stop report or is suspended with no blockers
- blockers are written into state

### 7. The doc still needs a route decision API

The route-control layer should not be a vague editorial note. It should resolve to
a small decision API, for example:

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

That makes the route controller something the supervisor can execute directly.
If the system cannot reduce route control to one of these actions, it does not
yet have a real controller.

### 8. The implementation must keep state and logs separate on purpose

The current design should use:

- `state/ai_contest_state.json` for durable route and challenge state
- `logs/ai-contest-*.jsonl` for audit trail
- `artifacts/challenges/<id>/...` for evidence and sidecar output

Do not let route truth exist only in logs. Logs are for audit. State is for
decision continuity.

This matters because the next loop cycle must be able to decide:

- what family is active
- what family was exhausted
- whether search is already required
- whether expert review already happened
- whether a public-search, expert-review, persistent-lane, or family-switch action was already emitted
- whether `NO_CANDIDATE` is still blocked

### 9. The doc should be wired back to the current code files

The intended integration points are already visible in the repo:

- `scripts/ai_contest_supervisor.py`
- `ctf_agents/submit/state_store.py`
- `ctf_agents/submit/flag_guard.py`
- `ctf_agents/sidecar/codex_validator.py`
- `runbooks/openai_expert_sidecar.md`
- `runbooks/codex_sidecar.md`

The route-control layer belongs above these pieces. It should not move submit
logic into the adapter, and it should not move route logic into `FlagGuard`.

Suggested file responsibilities:

- supervisor: route state, gate decisions, lane scheduling, persistence
- state store: submit safety, freeze, duplicate, rate-limit
- flag guard: candidate acceptance/rejection
- codex validator: sandbox / path / schema validation
- expert runbook: expert packet and output schema

### 10. Testing needs to prove route control, not just submit safety

The current test suite already proves a lot of submission safety. The missing
tests should prove route-control behavior.

Recommended test groups:

- route state round-trip survives restart
- `helper_bound_limit` becomes a cut, not another tuning cycle
- trivial roots increment the right counter and trigger cut conditions
- `capability_progress` does not reset stall counters
- public search is required after structural failure
- expert review is required before `NO_CANDIDATE`
- persistent lane blockers prevent premature `NO_CANDIDATE`
- no route decision can bypass the submit chain

If these tests do not exist, the design is still mostly descriptive.

### 11. Recommended rollout order

Do not try to land everything at once. The clean order is:

1. Add route-control fields to supervisor state.
2. Add route decision recording and gate triggers.
3. Add failure classification with `helper_bound_limit`.
4. Add public search ledger and disposition recording.
5. Add expert review packet and structured result ingestion.
6. Add persistent lane status and stop report.
7. Add `NO_CANDIDATE` exhaustion certificate.
8. Add tests for the above.
9. Update the loop prompt and runbook text to match the implemented state.

That ordering keeps the submit chain stable while the meta-control layer grows
around it.
