# Plan — Candidate 08: Plan the Workflow inside the router

Lifted from `Plans/05-09-architecture-deepening-roadmap.md` (Phase 08), refined
after reading the code. Branch `improve/08-router-plan-workflow`; draft PR base
`main`. This is the last phase we pursue (07 dropped — see roadmap note; 09 is
Speculative and out of scope).

## Problem

`master.plan` (`master.py:194-253`) assembles one `Workflow` by calling the
router seven-plus times — `route_workflow`, `workflow_persona` (suggested),
`apply_hysteresis`, `workflow_persona` (pending), `workflow_agents`,
`workflow_mode`, `workflow_evaluate` (+ conditional `evaluator` append),
`workflow_rounds`, `workflow_timeout_s` — and owns the combination logic and the
name-resolution rules. The runner then **re-validates** mode/agents invariants at
execute time (competitive must end in `evaluator`; debate ≥3; speculative ≥2).
The router is shallow (single-fact reads), and the mode/agents invariant is split
across master and the runner.

## Solution

One deep `router.plan_workflow(classification, *, current_persona, auto_mode,
pending_workflow) -> WorkflowPlan` that owns routing + config assembly + the
evaluator append + structural validation, returning a router-owned `WorkflowPlan`.

```python
@dataclass(frozen=True)
class WorkflowPlan:
    workflow_name: str
    persona: str                 # chosen, post-hysteresis / pending override
    agents: tuple[str, ...]      # evaluator already appended where applicable
    mode: str
    rounds: int | None
    timeout_s: int | None
    suggested_workflow: str | None   # for master's "classify" telemetry
    suggested_persona: str | None
```

`master.plan` shrinks to: call `plan_workflow`, emit the `classify` log (auto
mode), resolve the skill (`skills.resolve`), look up the persona model
(`personas.get`), and build the `Workflow`. Persona-model and skill resolution
stay in master because they read the persona/skill registries, not config.

### Why a `WorkflowPlan`, not a `Workflow`, comes back

`Workflow` lives in `master.py` and carries `model` (from `personas.get`) and
`skill_name` (from `skills.resolve`). If the router returned a `Workflow` it would
have to import `master` (cycle) and the persona/skill registries (coupling). A
router-owned `WorkflowPlan` carries only config-derived fields; master maps it to
`Workflow` by adding `model` + `skill_name`. No import cycle; the master/router
split stays clean (router = config, master = turn state + registries).

### Invariants at plan time

`plan_workflow` validates the structural shape (`competitive` ends with
`evaluator`; `debate` ≥3; `speculative` ≥2) and raises a clear error when the
Workflow is born. The runner **keeps** its existing raises as a registry-aware
backstop (it also checks the agent registry / `rank()` presence, which the router
cannot see). Belt and suspenders; no behavior removed.

## Sub-phases

- **08.1** Add `WorkflowPlan` + `router.plan_workflow` (compose the existing 7
  functions; move `_resolve_workflow_name` and `_PERSONA_DEFAULT_WORKFLOW` from
  master into router). Unit tests in `test_router.py`: auto-route, hysteresis
  keep/flip, `pending_workflow` override, evaluator append (and skip for
  competitive), rounds/timeout passthrough.
- **08.2** Structural invariant validation in `plan_workflow` (+ tests for the
  three malformed shapes). Runner raises stay as backstop.
- **08.3** `master.plan` delegates to `plan_workflow`; keeps the `classify` log,
  skill resolution, persona-model lookup, `before_plan`/`after_plan` events.
- **08.4** Full pytest + live smoke (routing, `/mode`, competitive/debate/
  speculative, governance).

## Behavior to preserve (guarded by tests, esp. `test_master`, `test_router`)

- `MasterAgent.plan(...)` stays the public method and returns the same `Workflow`
  for every existing case (the `test_master.plan` cases must pass unchanged).
- `/mode` override, hysteresis keep/flip, persona defaults, evaluator append,
  rounds/timeout, `before_plan`/`after_plan` payloads.
- The 7 router functions stay (external callers: repl `/mode`, evolution
  targets/sandbox/generator); `plan_workflow` composes them.
- Effective-config precedence (eval-override > promotion > file) and live-swap.

## Risks / ADR check (ADR-0003 / ADR-0008)

- classify → plan → execute pipeline unchanged; this deepens `plan` only.
- Precedence + live-swap untouched (still inside the same router reads).
- New plan-time validation only fires on malformed shapes that valid config never
  produces, so existing valid-config tests are unaffected.

## Out of scope

Phase 07 (VariantRow — dropped; its "bug" was disproven, see roadmap) and Phase
09 (Speculative). After 08 merges, the deepening roadmap is closed.

## Done when

`master.plan` is one `plan_workflow` call plus model/skill assembly; invariants
validated at plan time; the 7 functions still serve their other callers; full
pytest green; smoke passes.
