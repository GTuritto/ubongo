# Phase 10 — Evaluator + Critic + Persona Agents Formalized: Implementation Plan

Date: 2026-05-13
Branch: `phase-10-evaluator-critic` (off `main` at `48c6f83`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 10 (lines 966–994), Worker Agents table (105–129), Execution Modes (131–142), Governance Layer (144–152), Agent protocol (110–116), `agent_runs` / `workflow_runs` / `governance_decisions` schema (369–392).

## Goal

Three new workers land and one existing wrapper gets formalized:

1. **Evaluator Agent** — LLM-as-judge over the persona's response. Returns a confidence score (0.0–1.0) and a short list of flagged issues. Its score is the first real feeder for Phase 14's governance matrix; Phase 10 ships a minimum-viable stub in `governance/decision.py` that rejects on `confidence < 0.2` and otherwise still returns `auto`.
2. **Critic Agent** — contrarian frame. Argues against the prevailing answer. v0.1 Phase 10 uses it in one path only: when Evaluator confidence falls in the borderline band `[0.2, 0.6)`, Master invokes a second runner pass `(critic, persona)` so the persona can re-answer with the critique woven in. Same `workflow_run_id`; new `agent_runs` rows. Debate-mode multi-round use lands Phase 12.
3. **Persona Agents formalized** — `ArchitectPersona`, `OperatorPersona`, `CasualPersona` as subclasses of a shared `BasePersonaAgent` in `agents/personas.py`. Each takes no constructor args and binds its persona name internally. Registry names rename from `persona:architect` → `architect` (and the same for `operator`, `casual`). `workflows.yaml` updated; this is a one-time breaking rename inside v0.1, which makes the trace output and the `/agents` table read more cleanly.
4. **Master Agent uses Evaluator before governance** — `workflows.yaml` gains an `evaluate: true|false` flag per workflow; when true Master appends `evaluator` to `workflow.agents`. After the runner returns, the evaluator's score is passed to `governance.decide(..., evaluator_confidence=…)`. Borderline → second runner pass with Critic.
5. **`/trace <n>` REPL command** — operator-visible end-to-end view of recent turns: classification → workflow → agent runs (in order, with timings, tokens, confidence) → governance decision.

**Non-goals for Phase 10 (locked):** parallel/competitive/collaborative/debate/speculative modes (Phase 12), Coding/Execution/Repair agents (Phase 11), Phase 14's real risk thresholds, multi-turn debate, evaluator weighting/calibration, evaluator/critic involvement in summary or one-shot paths (they only live in `master.handle`).

## Why this plan exists

Three patterns Phase 10 locks in that Phases 11–14 inherit:

1. **Validator agents are first-class but distinguishable.** Phase 9 made every agent's text contribute to `prior_findings` and made the last successful agent's text the `WorkflowResult.text`. That's wrong once Evaluator/Critic ship: an Evaluator's text is a judgment, not a response. Phase 10 introduces a `composer: bool` class attribute on agents (default `False`; `True` on `BasePersonaAgent` only). The runner uses it to pick which agent's text is "the response" and to thread `prior_findings` correctly. Phase 11's Coding Agent will also be `composer=False` when followed by a persona; in `coding_session` the persona stays the composer.
2. **Evaluator confidence is a first-class field on `WorkflowResult`.** The runner harvests `result.confidence` from any agent and the highest-priority `AgentResult.confidence` (last evaluator wins for v0.1) becomes `WorkflowResult.evaluator_confidence`. Master passes it to `governance.decide`; the stored `governance_decisions.confidence` finally gets populated with something real (today it stores the classifier's confidence, which is semantically different). Phase 14 layers risk + reversibility on top without changing the call site.
3. **Borderline → Critic loop runs inside Master, not the runner.** The runner stays a linear, stateless dispatcher (sequential mode only in Phase 10; Phase 12 extends). Conditional re-dispatch lives in Master so the runner does not grow branching logic. Same workflow_run_id is reused so the trace shows the full story in one row.

## Branch + commit strategy

Branch: `phase-10-evaluator-critic` off `main` at `48c6f83` (current HEAD; Phase 9 + follow-ups merged).

Commits follow the spec's sub-phase letters:

- **10a** — `agents/evaluator.py`: `EvaluatorAgent`. LLM-as-judge prompt + JSON-shape response parsing + confidence score. Runner change: capture `result.confidence`; introduce `composer` attribute. Tests.
- **10b** — `agents/critic.py`: `CriticAgent`. Contrarian frame, contributes findings only (composer=False). Tests.
- **10c** — `agents/personas.py`: `BasePersonaAgent` + three subclasses + registry rename + workflows.yaml rename. Tests.
- **10d** — `master.py` + `governance/decision.py`: `evaluate` flag in workflows.yaml; Master appends `evaluator`; borderline-Critic re-dispatch; `WorkflowResult.evaluator_confidence`; governance stub honors `< 0.2 → reject`; governance_decisions row stores evaluator confidence. Tests.
- **10e** — `repl.py`: `/trace <n>` command; help line; `store.last_n_workflow_runs(n)` helper. Tests.
- **10f** — STATUS + smoke playbook Phase 10 section; help-line smoke scenario tweak.

## Sub-phases

### 10a — Evaluator Agent (`src/ubongo/agents/evaluator.py`)

**Purpose:** LLM-as-judge. Reads the persona's response (from `input.prior_findings`) and the user's question (from `input.message`) and returns a confidence score plus flagged issues.

**Tasks:**

1. Create `src/ubongo/agents/evaluator.py`:

   ```python
   class EvaluatorAgent:
       name = "evaluator"
       role = "LLM-as-judge: confidence, completeness, hallucination signals"
       composer = False  # text is judgment, not response

       def __init__(self) -> None:
           cfg = load_config()
           models = cfg.get("models", {})
           self.default_model = models.get("evaluator") or models.get("default", "")
           self.max_tokens = int(
               cfg.get("agents", {}).get("evaluator", {}).get("max_tokens", 400)
           )

       def run(self, input: AgentInput, context) -> AgentResult: ...
   ```

2. `run()` flow:

   **(i) Locate the candidate response.** `input.prior_findings[-1]` if non-empty (the last producer's text). If empty: return `AgentResult(text="", ok=False, error="evaluator_no_candidate", confidence=None, ...)`. Don't fail the workflow; runner records and continues.

   **(ii) Build the judge prompt.** Borrow the operator voice (concise, structured):

   ```
   {build_system_prompt("operator", agent_role="evaluator")}

   You are the Evaluator Agent. Judge the candidate response below against
   the user's question. Return ONLY a JSON object with this exact shape, no
   prose before or after:

   {
     "confidence": <float in [0.0, 1.0]>,
     "issues": [<short string>, ...]   // up to 5; empty list if none
   }

   Score rubric:
   - 0.9+ : answers the question directly, no hallucinated facts, complete.
   - 0.7–0.9 : answers correctly but with small gaps or unsupported claims.
   - 0.4–0.7 : partially answers; signals of hallucination or missing context.
   - 0.2–0.4 : largely wrong, misleading, or off-topic.
   - <0.2  : refuse-worthy: hallucinated, dangerous, or fundamentally broken.

   ## User question

   {input.message}

   ## Candidate response

   {prior_findings[-1]}
   ```

   Call `complete(...)` with one empty user turn (the judgment request is all in the system prompt) — or, equivalently, pass the candidate as the user turn. Pick the system-prompt route for parity with ResearchAgent and to keep history out of the judge's context.

   **(iii) Parse the JSON.** Use `json.loads()` on the response, tolerating leading/trailing whitespace and a single optional code-fence wrapper (` ```json ... ``` `). On any parse error: log `evaluator_parse_error` with the raw text, return `AgentResult(ok=False, error="evaluator_parse_error", confidence=None, ...)`. Don't crash. The runner records failure; the borderline-Critic path treats `None` as "no confidence available → skip critic".

   **(iv) Clamp + return.** Clamp confidence to `[0.0, 1.0]`. `issues` is a list of strings, truncated to 5. Return:

   ```python
   AgentResult(
       text=f"Confidence: {conf:.2f}. Issues: {'; '.join(issues) or 'none'}.",
       ok=True,
       model=completion.model,
       tokens_in=..., tokens_out=..., latency_ms=...,
       confidence=conf,
       metadata={"issues": issues, "raw": raw_text[:500]},
   )
   ```

   The text is short and human-readable (helps `/trace`). Issues live in metadata for the Critic prompt and for `/trace` deep inspection.

3. **No DB writes.** Evaluator is read-only. The runner records `agent_runs` and stores `confidence` in that row (column already exists).

4. **`composer = False` class attribute.** Read in 10a's runner change (next bullet).

5. **Runner change** (`src/ubongo/runner.py`):
   - When promoting `last_ok_result` to the value used by `WorkflowResult.text`, only consider agents with `getattr(agent, "composer", False) == True`. Track `last_composer_result` separately from `last_ok_result`.
   - Track `evaluator_confidence`: the last successful agent_run whose `result.confidence is not None`. Surface it on the new `WorkflowResult.evaluator_confidence: float | None` field.
   - Behavior when no composer ran successfully: fall back to current behavior (`text = LLM_FAILURE_MESSAGE`, `ok=False`). This is the no-persona-ever-ran case; should not happen in practice in Phase 10 but the fallback is cheap.
   - Always thread `prior_findings`: Evaluator/Critic text still gets appended so a later persona pass can read it.

6. **`WorkflowResult` dataclass** gains `evaluator_confidence: float | None = None`. Default keeps Phase 9 callsites green. Update `compose` and any `asdict` consumer (logging) accordingly (additive).

7. **settings.yaml** already lists `models.evaluator`. Add `agents.evaluator.max_tokens: 400`.

8. Tests in `tests/test_agents_evaluator.py` (~6):
   - Happy path: mocked LLM returns `'{"confidence": 0.83, "issues": []}'`; agent returns `AgentResult.ok=True`, `confidence=0.83`, `metadata["issues"]==[]`.
   - Code-fence tolerance: ` ```json\n{"confidence": 0.5, "issues": ["thin reasoning"]}\n``` ` parses to 0.5 and one issue.
   - Parse error: LLM returns `"sure, sounds good"` → `ok=False, error="evaluator_parse_error"`, no exception.
   - Clamp: LLM returns `'{"confidence": 1.7, "issues": []}'` → confidence == 1.0.
   - No candidate: `prior_findings=()` → `ok=False, error="evaluator_no_candidate"`.
   - Default model + max_tokens read from settings.

**Files added:** `src/ubongo/agents/evaluator.py`, `tests/test_agents_evaluator.py`.
**Files modified:** `src/ubongo/runner.py` (composer-aware text selection + evaluator_confidence harvesting), `src/ubongo/master.py` (`WorkflowResult.evaluator_confidence` field), `config/settings.yaml` (+`agents.evaluator.max_tokens`).

### 10b — Critic Agent (`src/ubongo/agents/critic.py`)

**Purpose:** Contrarian voice. Reads the persona's candidate response + (optionally) the Evaluator's flagged issues from prior_findings, returns a short brutal critique. Does not produce a final response; `composer=False`.

**Tasks:**

1. Create `src/ubongo/agents/critic.py`:

   ```python
   class CriticAgent:
       name = "critic"
       role = "contrarian challenger: argue against the prevailing answer"
       composer = False

       def __init__(self) -> None:
           cfg = load_config()
           models = cfg.get("models", {})
           self.default_model = models.get("critic") or models.get("default", "")
           self.max_tokens = int(
               cfg.get("agents", {}).get("critic", {}).get("max_tokens", 400)
           )

       def run(self, input: AgentInput, context) -> AgentResult: ...
   ```

2. `run()` flow:

   - Candidate response = `input.prior_findings[-1]` if non-empty; otherwise `ok=False, error="critic_no_candidate"`.
   - System prompt:

     ```
     {build_system_prompt("operator", agent_role="critic")}

     You are the Critic Agent. Your job is to argue against the candidate
     response below. Find the weakest claim. Name one assumption that is
     load-bearing but unsupported. If the candidate is correct, say so in
     one line and stop; do not invent disagreement.

     Output: max 5 short bullets. No preamble.

     ## User question

     {input.message}

     ## Candidate response

     {prior_findings[-1]}

     {optional: ## Evaluator flagged issues\n\n<bullets>}
     ```

     Optional Evaluator section appears only when `prior_findings[-2]` looks like an evaluator summary (starts with `"Confidence:"`). Cheap heuristic; safer than reaching back into agent_runs from inside an agent. False positives are harmless (the critic just sees a bit more context).

   - Call `complete()` with the user message as the single user turn. LLMError → `AgentResult(ok=False, error="critic_llm_error")`.

3. **`composer = False`.** Critic's text never becomes the WorkflowResult.text; it goes into prior_findings so a follow-up persona pass can weave the critique in.

4. **settings.yaml** already lists `models.critic`. Add `agents.critic.max_tokens: 400`.

5. Tests in `tests/test_agents_critic.py` (~4):
   - Happy path: mocked LLM returns a 3-bullet critique; agent returns `ok=True`, text has bullets, `composer=False`.
   - No candidate: empty `prior_findings` → `ok=False, error="critic_no_candidate"`.
   - Sees evaluator findings: `prior_findings=("Confidence: 0.45. Issues: thin reasoning.", "Architect's response …")` triggers the optional `## Evaluator flagged issues` section in the system prompt (assert via captured `complete` kwargs).
   - LLMError path: returns `ok=False, error="critic_llm_error"`, no exception escapes.

**Files added:** `src/ubongo/agents/critic.py`, `tests/test_agents_critic.py`.
**Files modified:** `config/settings.yaml` (+`agents.critic.max_tokens`).

### 10c — Persona Agents formalized (`src/ubongo/agents/personas.py`)

**Purpose:** Replace the single `PersonaAgent(persona_name)` wrapper with three concrete subclasses sharing a common base. Bare registry names (`architect`, `operator`, `casual`) replace the `persona:` prefix.

**Tasks:**

1. Refactor `src/ubongo/agents/personas.py`:

   ```python
   class BasePersonaAgent:
       """Concrete behavior shared by all persona agents.

       Subclasses set _persona_name as a class attribute; everything else
       (model loading, prompt build, LLM call, error handling) is inherited.
       """
       _persona_name: str = ""  # must be overridden
       role = "persona composer"
       composer = True

       def __init__(self) -> None:
           if not self._persona_name:
               raise TypeError("Persona subclass must set _persona_name")
           self.name = self._persona_name
           persona = get(self._persona_name)
           self.default_model = persona.model
           self._max_tokens = persona.max_tokens

       def run(self, input: AgentInput, context) -> AgentResult:
           # body moves over verbatim from existing PersonaAgent.run
           ...

   class ArchitectPersona(BasePersonaAgent):
       _persona_name = "architect"

   class OperatorPersona(BasePersonaAgent):
       _persona_name = "operator"

   class CasualPersona(BasePersonaAgent):
       _persona_name = "casual"
   ```

2. **Keep the existing `PersonaAgent(persona_name)` class as a deprecated shim** that proxies to the right subclass? No — the rename is internal and the codebase has one consumer (`runner.default_registry`). Delete the old wrapper class outright. Cleaner.

3. **Registry rename** in `src/ubongo/runner.py`'s `default_registry()`:

   ```python
   return {
       "research": ResearchAgent(),
       "memory": default_memory_agent,
       "evaluator": EvaluatorAgent(),
       "critic": CriticAgent(),
       "architect": ArchitectPersona(),
       "operator": OperatorPersona(),
       "casual": CasualPersona(),
   }
   ```

4. **`config/workflows.yaml` rename**:

   ```yaml
   workflows:
     technical_deep:    { agents: ["architect"], mode: sequential, evaluate: true }
     quick_action:      { agents: ["operator"],  mode: sequential, evaluate: false }
     casual_reply:      { agents: ["casual"],    mode: sequential, evaluate: false }
     supportive_reply:  { agents: ["casual"],    mode: sequential, evaluate: false }
     research_brief:    { agents: ["research", "architect"], mode: sequential, evaluate: true }
     coding_session:    { agents: ["architect"], mode: sequential, evaluate: true }
     debate_then_synthesize: { agents: ["architect"], mode: sequential, evaluate: true }
     speculative_brief: { agents: ["operator"],  mode: sequential, evaluate: false }
   default_workflow: casual_reply
   ```

   Casual + supportive + quick_action skip evaluation: their value is voice + speed, not correctness. Research + technical + coding + debate evaluate. `evaluate: false` is the default in code when the key is absent, so the explicit `false` lines are documentation.

5. **`router.workflow_persona()` update**: currently checks for `persona:` prefix. New rule: a workflow's "persona" is the name of any agent in the agents list that lives in the registry as a `BasePersonaAgent` subclass (i.e., one of `architect`, `operator`, `casual`). Implementation: hardcode the set `{"architect", "operator", "casual"}` in router for v0.1 (matches what `personas.get()` validates against anyway). New helper:

   ```python
   _PERSONA_AGENT_NAMES = ("architect", "operator", "casual")

   def workflow_persona(name: str) -> str:
       for agent in workflow_agents(name):
           if agent in _PERSONA_AGENT_NAMES:
               return agent
       return _DEFAULT_PERSONA
   ```

6. **`router.workflow_evaluate(name) -> bool`** new helper:

   ```python
   def workflow_evaluate(name: str) -> bool:
       data = _load_workflows()
       wf = (data.get("workflows", {}) or {}).get(name, {})
       return bool(wf.get("evaluate", False))
   ```

7. **`repl.py` VALID_PERSONAS** unchanged (still `("architect", "operator", "casual")`).

8. Tests in `tests/test_personas.py` (existing) + new `tests/test_agents_personas_classes.py` (~5):
   - `ArchitectPersona()` instantiates with `name="architect"`, `default_model` matching settings.yaml architect resolution.
   - Same for `OperatorPersona` and `CasualPersona`.
   - `BasePersonaAgent()` raises `TypeError` (no `_persona_name`).
   - `composer == True` on all three subclasses.
   - Subclass inherits the LLM-call body (mock `complete`; verify it's called with the architect's model).

9. **Migrate existing tests** that reference `persona:architect` / `PersonaAgent("...")`:
   - `tests/test_runner.py`: registry keys, workflow.agents fixtures.
   - `tests/test_repl_agents.py`: expected `/agents` rendered output.
   - `tests/test_master.py`: any workflow.agents assertions.
   - `tests/conftest.py`: only if it builds workflows directly.

**Files modified:** `src/ubongo/agents/personas.py` (refactor), `src/ubongo/runner.py` (registry rename), `src/ubongo/router.py` (workflow_persona + workflow_evaluate), `config/workflows.yaml` (rename + evaluate flag), `tests/test_runner.py`, `tests/test_repl_agents.py`, `tests/test_master.py`, `tests/test_router.py`.
**Files added:** `tests/test_agents_personas_classes.py`.

### 10d — Master Agent uses Evaluator + borderline → Critic

**Purpose:** Wire Evaluator into Master's plan/execute flow. Add the borderline-confidence Critic re-dispatch. Wire confidence into governance.

**Tasks:**

1. **`MasterAgent.plan`** changes:
   - Read `router.workflow_evaluate(wf_name)`. If true: `agents = (*agents, "evaluator")`.
   - No persona-list logic changes; existing `_resolve_workflow_name` remains the source of truth for persona/workflow pairing.

2. **`MasterAgent.handle`** changes (after the existing `result = self.execute(...)` call):

   ```python
   ec = result.evaluator_confidence  # float | None
   if (
       result.ok
       and ec is not None
       and _CRITIC_LOW <= ec < _CRITIC_HIGH
       and chosen in personas.VALID_PERSONAS
   ):
       events.dispatch("borderline_confidence", {"confidence": ec, "workflow_run_id": workflow_run_id})
       critic_workflow = Workflow(
           persona=chosen,
           model=workflow.model,
           skill_name=workflow.skill_name,
           execution_mode="sequential",
           agents=("critic", chosen),
       )
       retry_result = self.execute(critic_workflow, ctx, message, workflow_run_id=workflow_run_id)
       if retry_result.ok and retry_result.text:
           result = retry_result
   ```

   Constants `_CRITIC_LOW = 0.2`, `_CRITIC_HIGH = 0.6` live as module-level in `master.py` (Phase 14 will move them to `governance.yaml`).

3. **Governance call** updated:

   ```python
   decision = self.decide(classification, result, ctx)
   ```

   `MasterAgent.decide()` extended to forward `evaluator_confidence`:

   ```python
   def decide(self, classification, workflow_result, ctx):
       events.dispatch("before_govern", {...})
       try:
           decision = governance_decide(
               classification, workflow_result,
               evaluator_confidence=workflow_result.evaluator_confidence,
           )
       except Exception as exc:
           ...
       events.dispatch("after_govern", {"decision": asdict(decision)})
       return decision
   ```

4. **`governance/decision.py`** Phase 10 stub:

   ```python
   _REJECT_BELOW = 0.2  # Phase 14 will read from governance.yaml.

   def decide(classification, workflow_result, *, evaluator_confidence=None):
       if evaluator_confidence is not None and evaluator_confidence < _REJECT_BELOW:
           return Decision(
               action=Action.REJECT.value,
               reason=f"evaluator_confidence_below_floor:{evaluator_confidence:.2f}",
           )
       return Decision(action=Action.AUTO.value, reason=None)
   ```

   Every other turn still ends `auto`. Spec scenario 5 ("force evaluator < 0.2 → reject") is satisfied; Phase 14 layers risk + reversibility + intent without changing call sites.

5. **`governance_decisions.confidence` finally gets the right thing.** Master currently passes `classification.confidence` to `append_governance_decision`. Change to pass `result.evaluator_confidence or classification.confidence` (prefer evaluator's when present; fall back to classifier's so the existing column doesn't NULL out for non-evaluated workflows). Document this in the column-store call site with a one-line comment.

6. **REJECT response handling.** When `decision.action == "reject"`, Master overrides the composed text with a short refusal:

   ```python
   if decision.action == Action.REJECT.value:
       text = _REJECT_MESSAGE  # constant in master.py
       enqueue_source = "rejected"
   else:
       text = self.compose(workflow, result, ctx)
       enqueue_source = "response" if result.ok else "error"
   ```

   `_REJECT_MESSAGE = "I'm not confident enough in my answer to give it. Try rephrasing or breaking it down."` — one line, in Ubongo's voice (no em dashes per `UBONGO.md`).

   The MemoryAgent assistant-message commit + vault projection still run on rejection, so the trace and DB are coherent. (Alternative: skip the message append on reject. I lean keep it — the rejection IS the assistant turn and `/recall` should see it. **Open question 2 below.**)

7. **Logging.** `master_decision` log line gains `evaluator_confidence` and `critic_used: bool`. Existing fields stay.

8. Tests:
   - `tests/test_master.py` (modified):
     - When `workflows.yaml` has `evaluate: true`, `MasterAgent.plan` appends `"evaluator"` to `workflow.agents`.
     - When false, no append.
     - When evaluator returns confidence 0.45 (borderline), Master invokes runner a second time with `("critic", chosen)` and same `workflow_run_id`. Verify via mock runner.
     - When evaluator returns 0.85, no second pass.
     - When confidence 0.1, governance returns `reject`; response text == `_REJECT_MESSAGE`; `master_decision` log includes the reason.
   - `tests/test_governance_decision.py` (modified):
     - `decide(..., evaluator_confidence=0.1)` → `Decision(action="reject", reason="evaluator_confidence_below_floor:0.10")`.
     - `decide(..., evaluator_confidence=0.85)` → `Decision(action="auto", reason=None)`.
     - `decide(..., evaluator_confidence=None)` → `Decision(action="auto", reason=None)` (parity with Phase 8).

**Files modified:** `src/ubongo/master.py`, `src/ubongo/governance/decision.py`, `tests/test_master.py`, `tests/test_governance_decision.py`.

### 10e — `/trace <n>` REPL command

**Purpose:** Operator-visible end-to-end view of recent turns. One block per workflow_run with classification, workflow, agent runs in order with timings/confidence, and the governance decision.

**Tasks:**

1. **New store helper** in `src/ubongo/memory/store.py`:

   ```python
   def last_n_workflow_runs(n: int = 1) -> list[dict]:
       """Return the last N workflow_runs with their agent_runs and governance_decisions.

       Each dict shape:
         {
           id, conversation_id, message_id, classification (parsed JSON), workflow (parsed),
           execution_mode, outcome, started_at, ended_at,
           agent_runs: [ {agent, model, confidence, tokens_in, tokens_out, latency_ms,
                          outcome, started_at, ended_at, error?} ... ],
           governance: {action, reason, confidence} | None
         }
       """
   ```

   One query for the workflow_runs window, then batched `IN (...)` queries for agent_runs and governance_decisions joined back in Python. JSON columns parsed with `json.loads`. Ordered DESC by id.

2. **`/trace [n]` parser** in `repl.py` (mirror `_parse_queue_command`):

   ```python
   def _parse_trace_command(line: str) -> int | None:
       """Returns N from `/trace [N]`. Defaults to 1; None for malformed args."""
   ```

3. **Renderer** `_render_trace(n)`:

   ```
   Recent traces (last N):

   --- workflow_run #42 (conv 7, msg 113) ---
   started: 12:34:56  ended: 12:34:59  outcome: success
   classification: intent=technical tone=neutral task_type=question risk=low confidence=0.78
   workflow: persona=architect mode=sequential agents=[research,architect,evaluator]
   agents:
     research   architect-sonnet-4.5    success   ok=True   123ms   in=412/out=312   conf=—
     architect  architect-sonnet-4.5    success   ok=True   1840ms  in=820/out=540   conf=—
     evaluator  evaluator-sonnet-4.5    success   ok=True   210ms   in=380/out=44    conf=0.83
     memory     —                       success   ok=True   3ms     in=0/out=0       conf=—
   governance: action=auto  conf=0.83  reason=—

   --- workflow_run #41 ... ---
   ```

   Format borrowed from `_render_decisions_table` for consistency; column widths fixed; long agent names truncate at 14 chars.

4. **Wire `/trace` into the slash dispatch**:

   ```python
   if head == "trace":
       n = _parse_trace_command(stripped)
       if n is None:
           print(f"Usage: /trace [N]. {_HELP_COMMANDS}")
       else:
           print(_render_trace(n))
       continue
   ```

5. **Help line update**: `_HELP_COMMANDS` adds `/trace`. Phase-1 smoke scenario 1.7 expected help line gets `/trace` (mirror the Phase-9 `/agents` tweak).

6. Tests in `tests/test_repl_trace.py` (~5):
   - `_parse_trace_command("/trace")` → 1.
   - `_parse_trace_command("/trace 5")` → 5.
   - `_parse_trace_command("/trace foo")` → None.
   - `_render_trace(n)` with no workflow_runs → `"No traces yet."`.
   - End-to-end: write a synthetic workflow_run + agent_runs + governance_decisions in a temp DB; `_render_trace(1)` includes the workflow_run id, all agent rows in order, and the governance action.

7. Tests in `tests/test_memory_store.py` (modified): add `last_n_workflow_runs` roundtrip — insert one row + 3 agent_runs + 1 governance_decision, assert shape and ordering.

**Files modified:** `src/ubongo/repl.py`, `src/ubongo/memory/store.py`.
**Files added:** `tests/test_repl_trace.py`.

### 10f — STATUS + smoke playbook

**Tasks:**

1. Append Phase 10 section to `tests/manual/smoke_test.md` with the 6 scenarios in the spec's testing plan, expanded with concrete commands and DB queries (mirroring the Phase 9 section format). Include the help-line check (`/trace` should appear) and the `governance_decisions.confidence` query (should now show the evaluator's score, not the classifier's).
2. Update `STATUS.md`: Phase 10 row → Complete; "Overall" paragraph; LOC bump.
3. Update Phase 1 smoke scenario 1.7 expected help line to include `/trace`.
4. Update Phase 9 smoke scenario 9.3 expected `/agents` output: persona rows now read `architect`, `operator`, `casual` (no `persona:` prefix), and the table includes `evaluator` and `critic` rows.

**Files modified:** `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 10

```text
src/ubongo/
  agents/
    __init__.py
    base.py                          (unchanged)
    research.py                      (unchanged)
    memory.py                        (unchanged)
    personas.py                      (refactored: BasePersonaAgent + 3 subclasses)
    evaluator.py                     (new)
    critic.py                        (new)
  governance/
    decision.py                      (modified: reject-on-low-confidence stub)
  memory/
    store.py                         (modified: +last_n_workflow_runs)
  master.py                          (modified: evaluator wiring, borderline critic loop,
                                                 reject handling, WorkflowResult.evaluator_confidence)
  runner.py                          (modified: composer-aware text selection,
                                                 evaluator_confidence harvesting, registry rename)
  router.py                          (modified: workflow_persona uses persona name set,
                                                 +workflow_evaluate)
  repl.py                            (modified: /trace command + help line)
config/
  workflows.yaml                     (modified: persona name rename, +evaluate flag)
  settings.yaml                      (modified: +agents.evaluator.max_tokens,
                                                 +agents.critic.max_tokens)
tests/
  test_agents_evaluator.py           (new, ~6)
  test_agents_critic.py              (new, ~4)
  test_agents_personas_classes.py    (new, ~5)
  test_repl_trace.py                 (new, ~5)
  test_master.py                     (modified: evaluator append + borderline critic + reject)
  test_governance_decision.py        (modified: evaluator_confidence threshold)
  test_runner.py                     (modified: composer-aware text selection;
                                                registry name rename)
  test_repl_agents.py                (modified: bare persona names + 2 new agent rows)
  test_router.py                     (modified: workflow_persona via name set;
                                                workflow_evaluate roundtrip)
  test_memory_store.py               (modified: +last_n_workflow_runs)
Plans/
  phase-10-evaluator-critic.md       (new — this file)
STATUS.md                            (modified)
tests/manual/smoke_test.md           (modified — Phase 10 section + 1.7 + 9.3 tweaks)
```

Untouched: `classifier.py`, `delivery/queue.py`, `memory/compaction.py`, `memory/vault.py`, `skills.py`, `llm.py`, `oneshot.py`, `events.py`, `config/personas/*`, `config/skills/*`, `config/UBONGO.md`, `config/routing.yaml` (rules still map classification → workflow name; workflow → agents/mode/evaluate is workflows.yaml), `agents/research.py`, `agents/memory.py`, `agents/base.py`.

## Testing plan

Manual smoke (appended as § Phase 10 in `tests/manual/smoke_test.md`):

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 10.1 | Evaluator runs on technical workflow | `/architect`; `"What's a sensible retry strategy for OpenRouter calls?"` | Response composed by architect; `agent_runs` rows in order: `architect`, `evaluator`, `memory`. The `evaluator` row's `confidence` is a float in `[0, 1]`. `workflow_runs.workflow` JSON shows `agents: ["architect", "evaluator"]`. |
| 10.2 | Casual workflow skips evaluator | `/casual`; `"long day"` | `agent_runs` has only `casual` and `memory`; no `evaluator` row. |
| 10.3 | Persona Agent class rename | After 10.1: `sqlite3 data/ubongo.db "SELECT agent FROM agent_runs ORDER BY id DESC LIMIT 4"` | First rows include `architect` (bare), not `persona:architect`. |
| 10.4 | `/agents` updated table | `/agents` | Header `Registered agents:`; rows for `research`, `memory`, `evaluator`, `critic`, `architect`, `operator`, `casual` with role + model. |
| 10.5 | `/trace 1` after 10.1 | `/trace 1` | One block; shows classification fields, workflow.agents, agent rows in order with timings, evaluator confidence, governance action `auto`. |
| 10.6 | Borderline → Critic re-dispatch (test harness) | `uv run pytest tests/test_master.py::test_borderline_confidence_invokes_critic` | Pass. Evaluator returns 0.45 (mock); a second runner pass with `agents=("critic", "architect")` runs under the same `workflow_run_id`; final response replaces the first; trace shows 5 agent rows (`architect`, `evaluator`, `critic`, `architect`, `memory`). |
| 10.7 | Reject on very low confidence (test harness) | `uv run pytest tests/test_master.py::test_low_confidence_rejects` | Pass. Evaluator returns 0.1 (mock); `Decision.action == "reject"`; response is `_REJECT_MESSAGE`; `governance_decisions.action='reject'`. |
| 10.8 | Governance confidence column | After 10.1: `sqlite3 data/ubongo.db "SELECT action, confidence FROM governance_decisions ORDER BY id DESC LIMIT 1"` | `action=auto`; `confidence` matches the `evaluator` row's confidence (not the classifier's). |
| 10.9 | Regression: research_brief still works | `ubongo send "research what we discussed about caching" --persona architect` | `agent_runs` rows: `research`, `architect`, `evaluator`, `memory`. `workflow.agents` lists those four. |
| 10.10 | Help line includes `/trace` | `/help`-style banner check: any usage-line print in REPL | Includes `/trace` between `/agents` and `/reload`. |
| 10.11 | Pytest passes | `uv run pytest tests/` | All green (~191 from Phase 9 + ~20 new = ~211). |

## Out of scope for Phase 10 (do NOT build)

- Parallel / competitive / collaborative / debate / speculative modes — Phase 12. Runner still raises `NotImplementedError` for anything other than `sequential`.
- Multi-round debate (Critic ↔ Persona ↔ Critic ↔ Synthesis) — Phase 12.
- Coding / Execution / Repair agents — Phase 11.
- Real `composer.py` separate from PersonaAgent — Phase 12 if at all; v0.1 keeps composition inside the persona agent.
- Real risk evaluation (`governance/risk.py`), reversibility (`governance/reversibility.py`), full decision matrix (`governance/decision.py` per Phase 14), approval prompts (`governance/approval.py` — Phase 15).
- `ask_clarification` and `require_approval` action paths in `governance/decision.py` — Phases 14/15.
- `governance.yaml` parsing — Phase 14. The 0.2 / 0.6 thresholds are constants in `master.py` / `decision.py` for now.
- Evaluator calibration loop, per-persona evaluator tuning, multi-evaluator voting — out of v0.1.
- Critic invoked in any path other than borderline-evaluator-confidence in `master.handle` — Phase 12 wires the debate mode entry.
- `/trace` deep-inspection of `metadata.issues` JSON for Evaluator/Critic — Phase 10 surfaces the confidence number and one-line issues string; raw JSON dump waits.
- Skipping the assistant-message commit when `action=reject` (kept consistent with non-reject path for v0.1; revisit when approval flow lands in Phase 15).
- Migrating any further writes into MemoryAgent (user-message, session.upsert) — Phase 11+.
- Async runner — Phase 12.

## Open questions to confirm before I start

1. **`composer: bool` attribute on agents (recommended).** Phase 10 makes the runner distinguish "agents whose text is the response" from "agents whose text is judgment/critique." A class attribute (`composer = True/False`) read via `getattr(agent, "composer", False)` is purely additive; existing agents (Research, Memory) default to `False`. Alternative: encode it in the agent name (anything starting with `architect`/`operator`/`casual` is a composer). I lean the explicit attribute — names should not carry semantic load. OK?
2. **Reject path still commits the assistant message + vault note (recommended).** When `action == reject`, the response is `_REJECT_MESSAGE`; Memory Agent still commits it as the assistant turn and the vault still gets the note. This keeps `/recall` and the vault coherent (the rejection IS what the user saw). Alternative: skip the writes on reject and let `/decisions` carry the only record. I lean keep — the reject is a real turn. OK?
3. **Borderline band `[0.2, 0.6)` and reject floor `< 0.2` are constants in code, not config (recommended).** Phase 14 moves them to `governance.yaml`. Hardcoding in Phase 10 keeps the diff small. Alternative: introduce a minimal `governance.yaml` now with just these three values. I lean constants — anything `governance.yaml`-shaped pulls in Phase 14 scope. OK?
4. **Critic loop runs once max (recommended).** Even if the post-Critic persona retry produces another borderline evaluator score (which we'd need to add an evaluator to the critic-retry workflow to detect — we don't), no recursion. Phase 12 debate handles N-round arguments. OK?
5. **Bare persona names (`architect`, not `persona:architect`) (recommended).** Spec Phase 10 scenario 3 reads "`agent_runs.agent='architect'`" — exact match. Internal-only rename; no migration of existing rows needed (test DBs are recreated; production data is dev-only). OK?
6. **`governance_decisions.confidence` now stores evaluator confidence when present, classifier confidence otherwise (recommended).** Column purpose was always "confidence in this decision"; the classifier's confidence was a placeholder. Evaluator's score is the right source. Alternative: add a new column `evaluator_confidence` and leave `confidence` to the classifier. I lean reuse the column — Phase 14 will document this as the durable contract. OK?
7. **`workflows.yaml` gains `evaluate: true|false` (recommended).** Per-workflow flag; absent = false. Casual/operator-only workflows skip evaluation. Alternative: evaluator always runs; cheap (~300ms / ~0.001 USD). I lean per-workflow — keeps casual snappy. OK?
8. **`WorkflowResult.evaluator_confidence: float | None` (recommended)** field added with default `None`. Pure-additive dataclass change. The single field is enough for v0.1; `metadata.issues` lives on the agent_run row, not on WorkflowResult. OK?
9. **`/trace` defaults to N=1 (recommended).** A single recent turn is the common operator move during dev. `/trace 5` for the last five. Alternative: default 3. I lean 1 — paging up is cheap, scroll-bombing the terminal is annoying. OK?

If you don't push back on any, I'll go with the defaults above.

## Definition of done for Phase 10

- Six commits on `phase-10-evaluator-critic` (10a–10f).
- Smoke scenarios 10.1–10.11 pass; 10.11 pytest green.
- New tests: `test_agents_evaluator.py` (~6), `test_agents_critic.py` (~4), `test_agents_personas_classes.py` (~5), `test_repl_trace.py` (~5). Existing tests still pass with the updates listed.
- `tests/manual/smoke_test.md` Phase 10 section appended; scenario 1.7 help line updated to include `/trace`; scenario 9.3 expected `/agents` output updated for bare persona names + new agent rows.
- `STATUS.md` Phase 10 row → Complete; "Overall" paragraph refreshed; LOC count updated.
- Branch handed to you for merge. **Don't merge.**

---

(Verified: `origin/main` matches local `main` at `48c6f83`. Phase 9 fully merged. Branch `phase-10-evaluator-critic` does not yet exist.)
