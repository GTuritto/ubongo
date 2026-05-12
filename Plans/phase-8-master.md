# Phase 8 — Master Agent: Implementation Plan

Date: 2026-05-12
Branch: `phase-8-master` (off `main` at `bc47cf8`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 8 (lines 904–932), Master Agent section (95–103), event payload table (300–319), `workflow_runs` + `governance_decisions` schema (369–401), pipeline diagram (66–90).

## Goal

Introduce `src/ubongo/master.py::MasterAgent` as the single orchestration seam for every turn: `classify → plan → execute → decide → compose → enqueue`. **No user-visible behavior changes.** Phase 8 wraps the existing single-persona LLM flow in the Master Agent shape so Phase 9 workers, Phase 14 governance rules, and Phase 10 Evaluator can plug into the named-event surface without further restructuring. A new `/decisions` REPL command surfaces the per-turn classification + decision log.

## Why this plan exists

Three patterns Phase 8 locks in that the next ~6 phases inherit:

1. **Master Agent is THE orchestrator.** Today `repl.py::handle_text` is the de-facto orchestrator. After Phase 8, `MasterAgent.handle(message, …)` owns the flow and `handle_text` either disappears or becomes a one-line shim. Phase 9 will add real worker dispatch inside `execute()`; Phase 14 will replace the `decide()` stub with the real matrix. Both edits land cleanly only if the seam is right.
2. **`workflow_runs` + `governance_decisions` start getting written today.** The schema FK `governance_decisions.workflow_run_id NOT NULL REFERENCES workflow_runs(id)` forces both tables to be populated together. Spec lists Phase 9 as the formal write site for `workflow_runs` and Phase 14 for `governance_decisions`, but `/decisions` in Phase 8 has to read something — so Phase 8 writes minimal rows in both tables and later phases fill in the richer fields.
3. **The remaining named events get dispatched as passthroughs.** `before_plan`, `after_plan`, `before_execute`, `after_execute`, `before_govern`, `after_govern`, `before_compose`, `after_compose` ship with no registered handlers. They're the extension surface Phase 13 repair, Phase 14 governance, and v0.2 Telegram all hook off.

## Branch + commit strategy

Branch: `phase-8-master` off `main` at `bc47cf8`. Five commits:

- **8a** — `master.py` with `MasterAgent` class (`classify` / `plan` / `execute` / `decide` / `handle`) + supporting dataclasses (`Workflow`, `WorkflowResult`, `Decision`, `Response`, `Context`). Tests.
- **8b** — `governance/decision.py` stub returning `Action.AUTO` always; `governance/__init__.py`. Tests.
- **8c** — Migrate `repl.py` REPL loop + `oneshot.py` to call `MasterAgent.handle`. Delete `repl.handle_text` (or keep as a thin alias if tests scream). Tests updated.
- **8d** — `master_decision` log + persist `workflow_runs` and `governance_decisions` rows per turn. Tests.
- **8e** — `/decisions [N]` REPL command + tests. STATUS + smoke playbook Phase 8 section.

## Sub-phases

### 8a — `MasterAgent` class + workflow primitives (`src/ubongo/master.py`)

**Purpose:** Land the orchestration shape with no behavior change. The class wraps the *current* turn flow inside `classify → plan → execute → decide → handle`.

**Tasks:**

1. Create `src/ubongo/master.py` with these primitives:

   ```python
   @dataclass(frozen=True)
   class Context:
       conversation_id: int
       persona: str
       auto_mode: bool
       pending_skill: str | None

   @dataclass(frozen=True)
   class Workflow:
       persona: str                 # final persona for this turn (after hysteresis)
       model: str
       skill_name: str | None
       execution_mode: str          # always "sequential" in Phase 8
       agents: tuple[str, ...]      # always ("persona:<name>",) in Phase 8

   @dataclass(frozen=True)
   class WorkflowResult:
       text: str
       ok: bool
       tokens_in: int
       tokens_out: int
       model: str
       latency_ms: int

   @dataclass(frozen=True)
   class Decision:
       action: str                  # "auto" | "ask_clarification" | "require_approval" | "reject"
       reason: str | None

   @dataclass(frozen=True)
   class Response:
       text: str
       ok: bool
       persona: str
       skill_name: str | None
       delivery_token: queue.DeliveryToken
   ```

2. `class MasterAgent`:
   - `__init__(self)` — no state; the agent is a function-bag for now. Module-level `default_master = MasterAgent()` singleton, plus a module-level `handle(...)` that delegates.
   - `classify(self, message, ctx) -> Classification` — calls `classifier.classify(message)`. The `ctx` parameter is accepted for forward-compat (Phase 9 will use it); ignored in Phase 8.
   - `plan(self, classification, ctx) -> Workflow` — applies router + hysteresis (lifted from current `handle_text`), resolves skill, builds the `Workflow`. Dispatches `before_plan` before and `after_plan` after.
   - `execute(self, workflow, ctx, message) -> WorkflowResult` — calls the existing `_call_llm(persona, message, conv_id, skill_name)` logic. Dispatches `before_execute` / `after_execute`. (Phase 9 will replace the inline LLM call with workflow-runner dispatch.)
   - `decide(self, classification, workflow_result, ctx) -> Decision` — calls `governance.decision.decide(...)` (stub from 8b). Dispatches `before_govern` / `after_govern`.
   - `compose(self, workflow, workflow_result, ctx) -> str` — passthrough in Phase 8 (`return workflow_result.text`). Dispatches `before_compose` / `after_compose`. Phase 10 will introduce a real composer.
   - `handle(self, message, persona, auto_mode, pending_skill=None) -> Response` — runs the full pipeline:

     ```text
     ctx = Context(...)  # built from persona/auto_mode/pending_skill, conv_id resolved later
     classification = self.classify(message, ctx)
     conv_id = store.current_or_new_conversation(persona)
     user_msg_id = store.append_message(conv_id, "user", message, persona=...)
     ctx = ctx._replace(conversation_id=conv_id, persona=<chosen>)
     workflow = self.plan(classification, ctx)
     result = self.execute(workflow, ctx, message)
     # assistant message + session upsert + workflow_runs row (8d)
     decision = self.decide(classification, result, ctx)
     # governance_decisions row (8d)
     text = self.compose(workflow, result, ctx)
     # enqueue via queue.enqueue_for_delivery (same as today)
     return Response(text, result.ok, workflow.persona, workflow.skill_name, token)
     ```

   - The `decision.action` is logged but does NOT alter flow in Phase 8 (always `"auto"`).

3. Failure modes:
   - Classifier failure → already handled inside `classifier.classify` (returns fallback with `confidence=0`). Master passes through.
   - LLM failure → `result.ok=False`, source="error" on enqueue (same as today). `decide()` still runs and returns `auto`. Vault skipped (after_send_payload=None) — same as today.
   - Decision matrix raises (shouldn't in Phase 8, but) → wrap in try/except, log `master_decide_failed`, fall through to `Decision(action="auto", reason="fallback_on_error")`. Defensive scaffolding for Phase 14.

4. Tests in `tests/test_master.py` (~10 tests):
   - `MasterAgent.classify` delegates to `classifier.classify`.
   - `plan` applies hysteresis (mock classifier, current persona = architect, classifier suggests casual at 0.5 → workflow.persona stays architect).
   - `plan` honors pending_skill over suggested_skill.
   - `plan` dispatches before_plan / after_plan with workflow payload.
   - `execute` dispatches before_execute / after_execute.
   - `execute` calls the LLM and returns a WorkflowResult on success.
   - `execute` returns ok=False on LLMError; result.text = polite error message.
   - `decide` returns `Decision(action="auto")` (since 8b stub).
   - `compose` is passthrough (Phase 8).
   - `handle` end-to-end returns a Response with the expected fields and a non-None delivery_token on the happy path.

**Files added:** `src/ubongo/master.py`, `tests/test_master.py`.

### 8b — Decision matrix scaffold (`src/ubongo/governance/`)

**Purpose:** Establish the governance package and a `decide()` function that returns `auto` for everything. Phase 14 will replace the body with the real matrix.

**Tasks:**

1. Create `src/ubongo/governance/__init__.py` and `src/ubongo/governance/decision.py`:

   ```python
   from dataclasses import dataclass
   from enum import Enum

   class Action(str, Enum):
       AUTO = "auto"
       ASK_CLARIFICATION = "ask_clarification"
       REQUIRE_APPROVAL = "require_approval"
       REJECT = "reject"

   def decide(classification, workflow, *, evaluator_confidence=None) -> Decision:
       """v0.1 Phase 8 stub: always auto. Real matrix ships Phase 14."""
       return Decision(action=Action.AUTO.value, reason=None)
   ```

2. The function takes the same arguments Phase 14 will use (`classification`, `workflow`, `evaluator_confidence`), so the call site doesn't change when the rules land.
3. Tests in `tests/test_governance_decision.py` (~4 tests):
   - Returns `Action.AUTO` for low-risk technical.
   - Returns `Action.AUTO` even for high-risk destructive (8b is a stub; Phase 14 will override).
   - Returns `Action.AUTO` when `evaluator_confidence` is None / 0.0 / 1.0 (stub ignores it).
   - The `Action` enum string values match the schema check constraint vocabulary.

**Files added:** `src/ubongo/governance/__init__.py`, `src/ubongo/governance/decision.py`, `tests/test_governance_decision.py`.
**Decision flagged:** `Decision` lives in `master.py` (alongside `Workflow`, `WorkflowResult`); the `governance/` package imports it from there. Phase 14 may move it; for now keep coupling minimal.

### 8c — Migrate response path

**Purpose:** REPL loop and `oneshot.run` call `MasterAgent.handle` instead of `repl.handle_text`. Behavior identical.

**Tasks:**

1. In `repl.py`:
   - Remove `handle_text` (or keep a one-line shim if tests insist — see Q3 below). The current body logic now lives in `MasterAgent.handle` and its helpers.
   - REPL main loop ([repl.py:302](../src/ubongo/repl.py#L302)):

     ```python
     response = master.handle(stripped, persona, auto_mode, pending_skill=pending_skill)
     pending_skill = None
     print(response.text)
     queue.flush_delivered(response.delivery_token)
     if auto_mode:
         persona = response.persona
     ```

2. In `oneshot.py`:

   ```python
   response = master.handle(message, chosen, auto_mode=False)
   print(response.text)
   queue.flush_delivered(response.delivery_token)
   return 0 if response.ok else 1
   ```

3. `_call_llm`, `_build_message_history` move from `repl.py` to `master.py` (private helpers). REPL keeps `_run_summary`, `_render_skills_table`, `_render_queue_table`, `_reload_all`, slash handlers, parsers.
4. Update `tests/test_repl_summary.py`: the two `handle_text` direct-call tests become `master.handle` direct-call tests. The 5-tuple unpacking becomes `Response` attribute reads.
5. Update `tests/test_delivery_path.py`: no API change needed (oneshot.run is the entry point and its signature is stable), but verify the test still mounts vault and event probes against `master.handle`'s dispatch sites.

**Files modified:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `tests/test_repl_summary.py`, `tests/test_delivery_path.py`.

### 8d — Logging + persistence (`workflow_runs` + `governance_decisions`)

**Purpose:** Persist a row per turn in both tables and emit the `master_decision` log line.

**Tasks:**

1. Add helpers to `src/ubongo/memory/store.py`:
   - `append_workflow_run(conversation_id, message_id, classification, workflow, execution_mode, outcome, started_at, ended_at) -> int` — INSERT, returns `id`. `classification` and `workflow` are JSON-serialized.
   - `append_governance_decision(workflow_run_id, intent, risk, confidence, reversibility, action, approval_response=None, decided_at=None) -> int` — INSERT, returns `id`.
   - `last_n_governance_decisions(n=10) -> list[Row]` — JOIN with `workflow_runs` to surface the workflow JSON for display.
2. In `MasterAgent.handle`, after `execute()` returns:
   - Insert `workflow_runs` row with `execution_mode='sequential'`, `outcome='success' if result.ok else 'failure'`, `classification=asdict(classification)`, `workflow=asdict(workflow)`.
   - Call `decide()` → insert `governance_decisions` row with `intent=classification.intent`, `risk=classification.risk`, `confidence=classification.confidence`, `reversibility=None` (Phase 14 wires the skill's reversibility), `action=decision.action`, `approval_response=None`.
3. Emit `master_decision` log line at INFO:

   ```json
   {"event":"master_decision","intent":"technical","tone":"neutral","task_type":"question","risk":"low","confidence":0.9,"persona":"architect","skill":null,"execution_mode":"sequential","action":"auto","workflow_run_id":42,"decision_id":42,"conversation_id":1}
   ```

4. The existing `classify` log line in handle_text stays (it's emitted by the classifier path); the new `master_decision` line is an end-of-turn summary with the final action.
5. Tests in `tests/test_master.py` (extend):
   - After one `master.handle` call: `workflow_runs` has one row with the expected outcome; `governance_decisions` has one row with `action='auto'`.
   - On LLM failure: `workflow_runs.outcome='failure'`, `governance_decisions.action='auto'` still (Phase 8 doesn't downgrade).
   - `master_decision` log line is emitted with all the documented fields.

**Files modified:** `src/ubongo/master.py`, `src/ubongo/memory/store.py`, `tests/test_master.py`, `tests/test_memory_store.py` (extend with workflow_runs/governance_decisions roundtrip tests).

### 8e — `/decisions` REPL command + smoke + STATUS

**Purpose:** Operator-visible inspection of recent decisions.

**Tasks:**

1. In `repl.py`, add `_render_decisions_table(n=10)`:
   - Call `store.last_n_governance_decisions(n)`.
   - Empty → `No decisions yet.`
   - Otherwise: header `Recent decisions (last N):` then one line per row:
     `<id>  <HH:MM:SS>  <intent>  <persona>  <mode>  <risk>  <conf>  <action>`
     (persona comes from the joined `workflow_runs.workflow` JSON.)
2. Wire `/decisions` into slash dispatch with optional integer arg, parsed by `_parse_decisions_command` (mirroring `_parse_queue_command`). Update `_HELP_COMMANDS` to include `/decisions`.
3. Update the Phase-1 smoke scenario 1.7 expected help line to include `/decisions`.
4. Append Phase 8 section to `tests/manual/smoke_test.md` with the 5 scenarios in the testing plan below.
5. Update `STATUS.md`: Phase 8 row → Complete; "Overall" paragraph; LOC bump.

**Files modified:** `src/ubongo/repl.py`, `tests/test_repl_decisions.py` (new, ~6 tests), `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 8

```text
src/ubongo/
  master.py                                  (new — MasterAgent + Workflow/Result/Decision/Response/Context)
  governance/
    __init__.py                              (new)
    decision.py                              (new — stub: always auto)
  repl.py                                    (modified — handle_text removed; /decisions; orchestration now in master)
  oneshot.py                                 (modified — calls master.handle)
  memory/store.py                            (modified — workflow_runs + governance_decisions writers)
tests/
  test_master.py                             (new — MasterAgent unit + integration tests; ~13 tests after 8d)
  test_governance_decision.py                (new — ~4 tests)
  test_repl_decisions.py                     (new — /decisions rendering tests; ~6 tests)
  test_repl_summary.py                       (modified — handle_text → master.handle)
  test_memory_store.py                       (modified — +workflow_runs / +governance_decisions)
  test_delivery_path.py                      (verified — calls oneshot.run; no API churn)
Plans/
  phase-8-master.md                          (new — this file)
STATUS.md                                    (modified)
tests/manual/smoke_test.md                   (modified — Phase 8 section + 1.7 help-line tweak)
```

Untouched: `classifier.py` (still emits before_classify / after_classify on its own; Master invokes it), `delivery/queue.py`, `memory/vault.py`, `memory/compaction.py`, `skills.py`, `router.py` (becomes a private helper called from `master.plan`), `agents/personas.py`, `context.py`, `events.py`, `llm.py`, `config/`.

## Testing plan

Manual smoke (appended as § Phase 8 in `tests/manual/smoke_test.md`):

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 8.1 | Behavior parity | Same prompts as Phase 7 baseline (a technical question and a casual line) | Same shape of response; persona voice unchanged; queue still populated; vault still written. |
| 8.2 | `master_decision` log | `rm -f data/ubongo.db`; `ubongo send "design a circuit breaker" --persona architect 2>/tmp/p8.err`; `grep master_decision /tmp/p8.err` | one JSON line with `intent`, `persona=architect`, `execution_mode=sequential`, `risk` set, `action=auto`, `workflow_run_id` and `decision_id` populated. |
| 8.3 | `workflow_runs` + `governance_decisions` populated | After 8.2: `sqlite3 data/ubongo.db "SELECT id, execution_mode, outcome FROM workflow_runs"`; `sqlite3 data/ubongo.db "SELECT workflow_run_id, action FROM governance_decisions"` | each has one row; `execution_mode='sequential'`, `outcome='success'`, `action='auto'`, FK matches. |
| 8.4 | `/decisions` table | Send 3 messages; REPL `/decisions` | header `Recent decisions (last 10):`, 3 rows newest-first with id, time, intent, persona, mode, risk, conf, action. |
| 8.5 | `/decisions N` | `/decisions 1` | exactly one row (most recent). `/decisions abc` → `Usage: /decisions [N]. …`. |
| 8.6 | High-risk passthrough (Phase 14 will tighten) | Mock the classifier to return `risk=high` (or send a destructive-looking prompt); inspect log | `master_decision` shows `risk=high`, `action=auto` (stub doesn't gate). |
| 8.7 | Classifier crash | `OPENROUTER_API_KEY=sk-or-v1-bogus ubongo send "hi" --persona casual` | response: polite error; `master_decision` line with `confidence=0.0`, `action=auto`; `workflow_runs.outcome='failure'`; `governance_decisions` row exists. |
| 8.8 | Pytest passes | `uv run pytest tests/` | all green (current 142 + ~23 new ≈ 165). |

## Out of scope for Phase 8 (do NOT build)

- Real worker agents (`agents/research.py`, `agents/coding.py`, etc.). Phase 9.
- `agents/base.py` Agent protocol + AgentInput/AgentResult. Phase 9.
- `agent_runs` writes. Phase 9.
- Workflow Runner with parallel/competitive/collaborative/debate/speculative modes. Phase 12.
- Real `composer.py`. Phase 10.
- Evaluator Agent / confidence feeding into decisions. Phase 10.
- Real governance rules (risk thresholds, reversibility checks, approval gates). Phases 14–15.
- `governance.yaml` parsing. Phase 14.
- `routing.yaml` / `workflows.yaml` config files. Phase 9 (workflows) / Phase 12 (modes).
- `approval_response` writes / `/approve` flow. Phase 15.
- `/trace <n>` command. Phase 10.
- `/agents` command. Phase 9.

## Open questions to confirm before I start

1. **`MasterAgent` is a class with a module-level singleton + module-level `handle(...)` delegate (recommended).** Alternative: pure-function module (no class). Spec explicitly says "class"; I'll keep the class but expose a free function so callers don't carry instance state. OK?
2. **`Response` is a `@dataclass(frozen=True)` (recommended) vs a 5-tuple.** Cleaner attribute access in callers; tests stay readable. Phase 7's `DeliveryToken` already set the dataclass precedent. OK?
3. **Delete `repl.handle_text` outright (recommended) vs keep as a thin shim.** Two tests (`test_repl_summary.py`) call it directly; I'll update them to `master.handle` and delete the function. Alternative: keep a one-line shim `handle_text = lambda *a, **kw: master.handle(*a, **kw)` so the call sites can survive. I lean delete — Phase 9+ will rewrite anyway and a stale shim is noise. OK?
4. **Phase 8 writes both `workflow_runs` and `governance_decisions` (recommended).** Spec hedges (Phase 9 owns workflow_runs, Phase 14 owns governance_decisions), but the schema FK forces both. `/decisions` needs data. Alternative: skip persistence in Phase 8 and `/decisions` reads from logs only. I lean persist — it's where the data belongs and downstream phases add columns rather than introducing new write paths. OK?
5. **`master_decision` log emitted at INFO with the documented payload (recommended).** Spec scenario 2 mandates it; format is up to us. Proposing one JSON line per turn with intent / tone / task_type / risk / confidence / persona / skill / execution_mode / action / workflow_run_id / decision_id / conversation_id. OK?
6. **The dispatch sites for `before_plan` / `after_plan` / `before_execute` / `after_execute` / `before_govern` / `after_govern` / `before_compose` / `after_compose` ship as passthroughs (no registered handlers).** They're scaffolding for Phase 13/14. OK?
7. **Classification + Decision still happen on the LLM-failure path (recommended).** When LLM fails, we still record `outcome='failure'` and `action='auto'`. Alternative: skip both tables on failure. I lean record — observability of failed turns matters, and Phase 13 Repair needs to look at failed workflow_runs. OK?
8. **`/decisions` default N = 10, optional integer arg (mirrors `/queue`).** OK?

If you don't push back on any, I'll go with the defaults above.

## Definition of done for Phase 8

- Five commits on `phase-8-master` (8a, 8b, 8c, 8d, 8e).
- Smoke scenarios 8.1–8.7 pass; 8.8 pytest green.
- `tests/test_master.py` (~13), `tests/test_governance_decision.py` (~4), `tests/test_repl_decisions.py` (~6) added; existing 142 still pass (with `test_repl_summary.py` updated to use `master.handle`).
- `tests/manual/smoke_test.md` Phase 8 section appended; scenario 1.7 help-line updated to include `/decisions`.
- `STATUS.md` Phase 8 row → Complete; "Overall" paragraph refreshed; LOC count updated.
- Branch handed to you for merge. **Don't merge.**

---

(Verified: `origin/main` is in sync with local `main` at `bc47cf8`. No outstanding push.)
