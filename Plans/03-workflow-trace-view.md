# Plan — Candidate 03: Deepen the workflow-trace read into a view

Architecture-review deepening candidate **03** (Strong) from
[`docs/architecture-review-2026-06-05.md`](../docs/architecture-review-2026-06-05.md).
Branch: `improve/03-workflow-trace-view` (off `main`).

## Problem

`store.last_n_workflow_runs` ([memory/store.py:871-1006](../src/ubongo/memory/store.py))
runs 4 queries (workflow_runs + agent_runs + governance_decisions + repair_runs), groups
them, and returns a **raw nested dict** per run. The `/trace` caller
([repl.py:682-771](../src/ubongo/repl.py)) then reconstructs the schema by hand over ~90
lines: `r["classification"]`, `wf.get("agents")`, `r["governance"]`, and — the real leak —
it re-groups `repair_runs` by agent (`repair_runs_by_agent`), tracks which it has rendered
(`printed_repairs`), and attaches each repair line only under the **failing** agent_run row.
That grouping is data-shaping the store should own; today it lives in the renderer.

The interface is nearly as complex as the implementation (a nested dict every caller must
learn to decode) — the definition of a shallow module.

## Solution

`last_n_workflow_runs` returns a list of typed, render-ready **`WorkflowTrace`** views. The
store absorbs the join *and* the repair→failing-agent grouping; `/trace` reads fields.

### New view types — `src/ubongo/memory/trace.py`

A small sibling module (keeps the view types out of the already-large `store.py`; candidate
06 flags `store.py` size). `store.last_n_workflow_runs` builds and returns these.

```python
@dataclass(frozen=True)
class RepairRunView:
    agent: str; failure_kind: str; original_error: str | None
    strategy_attempted: str; peer_agent: str | None; override_model: str | None
    attempt_index: int; outcome: str; started_at: str; ended_at: str | None

@dataclass(frozen=True)
class AgentRunView:
    agent: str; model: str | None; confidence: float | None
    tokens_in: int; tokens_out: int; latency_ms: int | None
    outcome: str; started_at: str; ended_at: str | None
    error: str | None; retried: bool
    repair_runs: tuple[RepairRunView, ...]   # attached to the FAILING row by the builder

@dataclass(frozen=True)
class GovernanceView:
    id: int; action: str; confidence: float | None
    intent: str | None; risk: str | None; reversibility: str | None

@dataclass(frozen=True)
class WorkflowTrace:
    id: int; conversation_id: int | None; message_id: int | None
    classification: dict; workflow: dict       # parsed JSON blobs, many optional keys
    execution_mode: str; outcome: str
    started_at: str; ended_at: str | None
    agent_runs: tuple[AgentRunView, ...]
    governance: GovernanceView | None
    # convenience accessors so callers read t.agents, not t.workflow["agents"]:
    @property
    def persona(self) -> str | None: ...
    @property
    def agents(self) -> list[str]: ...
    @property
    def intent(self) -> str | None: ...      # + tone, task_type, risk, cls_confidence
```

`classification`/`workflow` stay dicts (free-form JSON with many optional keys; fully typing
them is out of scope and low-value) but the leaky `wf["agents"]` / `cls["intent"]`
navigation is wrapped in accessors.

### The grouping moves into the builder

`last_n_workflow_runs` keeps its 4 queries, then — instead of returning a top-level
`repair_runs` list — walks each run's agent_runs in order and attaches each agent's
repair_runs under the **first failing** agent_run for that agent (exactly the
`repair_runs_by_agent` + `printed_repairs` logic the renderer does today). Non-failing rows
get an empty `repair_runs`. The top-level `repair_runs` key is dropped: the only consumer is
`/trace`, which renders them per failing agent, and orphan repair_runs (no failing agent_run
in the trace) are not rendered today either, so no display changes.

### Caller shrinks — `repl.py::_render_trace`

- Reads attributes: `t.id`, `t.agents`, `t.persona`, `t.intent`, `t.governance.action`,
  `ar.outcome`, `ar.retried`, `ar.repair_runs`.
- Deletes `repair_runs_by_agent`, `printed_repairs`, and the attach condition. The agent
  loop just does `for rr in ar.repair_runs: <render line>` — `repair_runs` is non-empty
  only on the failing row the builder chose, so rendering is identical.
- Rendered output (every line, byte-for-byte) is unchanged — guarded by the
  `_render_trace` output tests.

## Tests

- **Unchanged (guard render output):** `tests/test_repl_trace.py` render tests —
  `test_render_trace_includes_classification_workflow_agents_governance`,
  `test_render_trace_renders_repair_line_under_failing_agent` (13.10),
  `test_render_trace_multi`, `test_render_trace_no_rows`. These seed the DB and assert on
  the rendered string; output is preserved so they pass as-is.
- **Updated to attribute access (data-shape tests):**
  - `test_repl_trace.py::test_last_n_workflow_runs_orders_desc_and_joins_agent_runs` —
    `r["id"]`→`r.id`, `rows[0]["agent_runs"]`→`.agent_runs`,
    `rows[0]["governance"]["action"]`→`.governance.action`.
  - `test_memory_store.py::test_last_n_workflow_runs_includes_repair_runs` — repair_runs
    now nest under the failing agent_run, so seed a failing `critic` agent_run and assert
    `rows[0].agent_runs[i].repair_runs[0].peer_agent == "architect"` (a truer test of the
    rendered behavior than the old top-level check).
  - `test_last_n_workflow_runs_returns_empty_when_db_empty` (`== []`) stays.
- **New:** a builder unit test in `tests/test_memory_store.py` — repair_run attaches to the
  failing agent_run, not the peer's success row; a non-failing row has empty `repair_runs`.
- Full suite green (`pytest`), then smoke `/trace` rows (10.5, 11.11, 13.10).

## Risks / ADR check

- **No ADR touched.** The review marks this "small, safe, no ADR." It is a read-path
  reshape; no writer, no schema, no governance/runner change.
- **Blast radius:** `store.last_n_workflow_runs` + new `memory/trace.py` + `repl._render_trace`
  + 2 test updates. The single caller and render-output tests make it low-risk.
- **Dropped top-level `repair_runs`:** acceptable — `/trace` is the only consumer and it
  renders per failing agent; orphans were never displayed.

## Out of scope

Candidates 02, 04, 05, 06. Fully typing the `classification`/`workflow` JSON blobs.

## Done when

- `last_n_workflow_runs` returns `WorkflowTrace[]`; `_render_trace` reads fields with no
  hand-rolled grouping; `/trace` output byte-identical.
- Render tests green unchanged, data-shape tests updated + green, new builder test green,
  full suite green, smoke `/trace` rows pass.
- Draft PR opened against `main`, marked ready once the above hold.
