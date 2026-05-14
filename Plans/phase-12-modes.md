# Phase 12 — Execution Modes (all six): Implementation Plan

Date: 2026-05-14
Branch: `phase-12-modes` (off `main` at `1f023d2`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 12 (lines 1027–1058), Execution Modes table (131–142), Worker Agents table (105–129), Workflow Runner (Phase 9 design as currently in `runner.py`), Phase 10 composer rule + evaluator confidence (Plans/phase-10-evaluator-critic.md).

## Goal

Light up the five execution modes that have been "raises NotImplementedError" since Phase 9. After Phase 12, the WorkflowRunner supports:

1. **Sequential** (Phase 9 baseline; unchanged behavior).
2. **Parallel** — every agent in `workflow.agents` runs concurrently via `asyncio.gather`. They all see the same `prior_findings` (the user message + summary). Composer wins for `WorkflowResult.text` (last-composer-wins still holds; in parallel mode the persona usually runs alone, so this is unambiguous). Latency drops to roughly `max(agent_i_latency)` rather than `sum(...)`.
3. **Competitive** — N candidate agents (typically two persona/coding variants) get the same input and run in parallel. The Evaluator gets ALL N candidate texts and picks one with reasoning. `WorkflowResult.text` is the winner. New `EvaluatorAgent.rank(input.metadata["candidates"]) -> {winner_index, reason, scores}` entry point.
4. **Collaborative** — all agents in `workflow.agents` run in parallel; each one's role drives its specialization (Research = facts, Critic = risks). Texts are merged STRUCTURALLY into a single document under `## <agent_name>: <agent_role>` headings. `WorkflowResult.text` = merged document.
5. **Debate** — exactly two debater agents (the first two in `workflow.agents`) argue for N rounds (default 2; `workflow.rounds` overrides). Round 1 = A speaks, round 2 = B sees A and argues against, round 3 = A sees B, ... alternating. Then the LAST agent in `workflow.agents` (must be a composer; typically `architect`) synthesizes — sees the full debate transcript in `prior_findings`, returns the synthesis. `WorkflowResult.text` = synthesizer's output.
6. **Speculative** — exactly two agents in the producer slot: `cheap` (fast, e.g., `casual` or `operator`) and `strong` (slow, e.g., `architect`). Both run concurrently via `asyncio.gather` with a hard total timeout (default 10s). Cheap's result is returned as the response immediately. If strong finishes within the timeout AND its text materially differs from cheap's (judged by the Evaluator with a "do they agree?" prompt), the runner appends a follow-up correction block to the response: cheap's text first, separator, then `[Correction (slower model):] <strong's text>`. v0.1 keeps this in-turn (single `master.handle` return) to avoid a cross-turn pending-tasks queue; the "background" framing waits for Phase 13's repair work to land async infra. **Open question 6.**

This is the **end of Tier 2 (Multi-Agent System)**. After Phase 12, the workflow runner is feature-complete; the rest of v0.1 builds on top.

**Non-goals for Phase 12 (locked):**
- Real cross-turn background tasks (true fire-and-forget speculative). Phase 13 may revisit when the Repair Agent gets activation logic. Phase 12's speculative is in-turn with a timeout.
- Async at the top of the stack (`master.handle`, `repl.run`, `oneshot.run` stay sync). The runner internally goes async; `master.execute` calls `asyncio.run(...)` to bridge.
- A `--mode <workflow>` CLI flag for one-shot. REPL `/mode` only in Phase 12; one-shot users keep the routed default.
- Auto-routing classifications to the new modes (e.g., classifier picks `speculative` for low-stakes prompts). Phase 12 modes are activated by workflows declared in `workflows.yaml` and selected via `routing.yaml` (already wired) or the `/mode` debug command. No new routing rules ship in Phase 12.
- Custom multi-debater (3+ sides), structured argumentation tracks, or per-round role swapping. Two-sided debate only.
- Mode selection by the Master Agent at runtime ("auto-promote to debate when …"). Phase 14 may cover this.

## Why this plan exists

Three patterns Phase 12 locks in that Phase 13 + 16+ inherit:

1. **The runner is async-internally, sync-externally.** Going full-async would force `master.handle` and `repl.run` to either become coroutines or to call `asyncio.run` at every entry. Both options leak async into call sites that don't need it. Instead, `WorkflowRunner._execute_async(...)` is a coroutine; the existing sync `WorkflowRunner.execute(...)` calls `asyncio.run(self._execute_async(...))`. Concurrency lives where it pays off (parallel/competitive/collaborative/debate/speculative); the rest of the stack stays simple. Phase 16's GP loop can adopt the same pattern (async eval workers, sync orchestration).
2. **Mode-specific behavior dispatches off `workflow.execution_mode`, not off agent class.** The runner picks a strategy function (`_run_sequential`, `_run_parallel`, `_run_competitive`, etc.) per workflow run. Each strategy is a thin coroutine: dispatch agents, persist `agent_runs`, return a `WorkflowResult`. Easy to add a seventh strategy in a future phase (e.g., `_run_supervisor` for Phase 14 governance) without growing the existing five.
3. **Evaluator gets a `rank` entrypoint distinct from `run`.** Today the Evaluator scores ONE candidate against the user question. Competitive mode needs it to pick a winner from N. Adding a new method (rather than overloading `run` with a "candidates" mode) keeps both call sites simple. The runner's `_run_competitive` calls `evaluator.rank(...)` directly; the existing `evaluator.run` keeps its existing post-persona scoring shape.

## Branch + commit strategy

Branch: `phase-12-modes` off `main` at `1f023d2` (HEAD; Phases 0-11 + docs refresh + chore commits landed).

Per `feedback_phase_branch_open_draft_pr.md`: push the branch and open a **draft PR** immediately after this plan commit, base `main`. PR title: `Phase 12 — Execution Modes (all six)`. PR stays draft until 12h lands.

Nine commits (Plan + 12a–12h):

- **12a** — async runner foundation + parallel mode. `_execute_async` coroutine; `_run_sequential` extracted; `_run_parallel` added. Tests.
- **12b** — competitive mode + `EvaluatorAgent.rank`. `_run_competitive`. Tests.
- **12c** — collaborative mode. `_run_collaborative` with structural merge. Tests.
- **12d** — debate mode. `_run_debate` with N-round alternation + synthesis. Tests.
- **12e** — speculative mode. `_run_speculative` with in-turn timeout + correction concat. Tests.
- **12f** — workflows.yaml mode declarations: example workflows for each mode (`research_brief_parallel`, `coding_competitive`, `brief_collaborative`, `debate_then_synthesize`, `speculative_brief`). Tests.
- **12g** — `/mode <workflow>` REPL command + `pending_workflow_name` plumbing. Tests.
- **12h** — STATUS + smoke playbook Phase 12 section + scenario 1.7 help-line tweak (`/mode`).

## Sub-phases

### 12a — Async runner foundation + Parallel mode

**Purpose:** Convert the sync `WorkflowRunner.execute` body into an async coroutine, with the public sync method as a thin `asyncio.run` bridge. Extract sequential dispatch into `_run_sequential` (a strategy method) and add `_run_parallel`.

**Tasks:**

1. **`src/ubongo/runner.py`** restructure:

   ```python
   class WorkflowRunner:
       def __init__(self, registry: dict[str, Agent]): ...

       def execute(self, workflow, context, message, workflow_run_id=None):
           """Sync entry point. Bridges to the async strategy via asyncio.run."""
           return asyncio.run(
               self._execute_async(workflow, context, message, workflow_run_id)
           )

       async def _execute_async(self, workflow, context, message, workflow_run_id):
           summary, history = build_message_history(...)
           strategies = {
               "sequential":   self._run_sequential,
               "parallel":     self._run_parallel,
               # competitive/collaborative/debate/speculative land in 12b-12e
           }
           strategy = strategies.get(workflow.execution_mode)
           if strategy is None:
               raise NotImplementedError(f"Phase 12: unknown mode {workflow.execution_mode}")
           return await strategy(workflow, context, message, summary, history,
                                  workflow_run_id)
   ```

2. **Extract `_dispatch_agent` to be async-friendly.** Today's `_dispatch_agent` is sync; agents themselves implement sync `run(input, context) -> AgentResult`. v0.1 keeps the agent protocol sync (per Phase 9's open question 1; no agent today has any reason to be async — the LLM call inside is sync via `complete()`). The runner wraps each sync `agent.run(...)` in `asyncio.to_thread(...)` when it needs to fan out. So:

   ```python
   async def _dispatch_agent_async(self, *, agent, agent_name, ..., retried, override_model):
       # same body as today's _dispatch_agent, but the actual agent call is:
       try:
           result = await asyncio.to_thread(agent.run, input, context)
       except Exception as exc: ...
       # everything else identical (events, agent_runs row, etc.)
   ```

   The existing sync `_dispatch_agent` is removed; the only call site (the sequential loop) moves to the async strategy.

3. **`_run_sequential`** is a coroutine that does today's loop body verbatim, awaiting each `_dispatch_agent_async` call serially. The Repair retry path also moves into `_run_sequential` (no behavior change). **Sequential mode is the regression gate**: every existing test must pass without changes.

4. **`_run_parallel`**:

   ```python
   async def _run_parallel(self, workflow, context, message, summary, history, workflow_run_id):
       prior_findings: list[str] = []  # always empty in parallel mode (all agents see only user msg)
       tasks = [
           self._dispatch_agent_async(
               agent=self.registry[name], agent_name=name,
               message=message, history=history, summary_text=summary,
               prior_findings=prior_findings, workflow=workflow, context=context,
               workflow_run_id=workflow_run_id, override_model=None, retried=False,
           )
           for name in workflow.agents if name != "repair"
       ]
       results = await asyncio.gather(*tasks)
       # Compose: pick last-composer-wins by INDEX in workflow.agents ordering
       last_composer_result = None
       evaluator_confidence = None
       for name, res in zip(workflow.agents, results):
           if res.ok and getattr(self.registry.get(name), "composer", False):
               last_composer_result = res
           if res.confidence is not None:
               evaluator_confidence = res.confidence
       ...
   ```

   - **Parallel mode does NOT support Repair retry.** A failure in parallel is reported but not retried in v0.1 — the retry semantics in a fan-out are ambiguous (cancel the others? wait?). Phase 13 may revisit. Document explicitly.
   - **Parallel mode does NOT thread findings.** Every agent gets `prior_findings = ()`. If a workflow needs A → B with B reading A's text, it should be sequential.
   - The evaluator (when appended via `evaluate: true`) STILL runs in parallel with the rest, which is wrong (it should see the persona's response). For v0.1 Phase 12: parallel mode skips the auto-appended evaluator; if the user wants Evaluator-on-parallel, they declare a sequential post-step (out of scope) or use `competitive` instead. **Open question 1.**

5. **`master.execute`** unchanged. The runner's sync `execute` keeps its signature; master calls it the same way.

6. Tests in `tests/test_runner.py` (modified, +5 parallel tests):
   - Parallel: 2 agents both succeed; `WorkflowResult.ok`; latency assertion via mocked sleeps shows max-not-sum.
   - Parallel with one failing: `WorkflowResult.ok=False`; second agent's text still in agent_runs.
   - Parallel does not retry on failure (in contrast to sequential's Repair path).
   - Parallel agents see `prior_findings == ()`.
   - Parallel composer-pick uses `workflow.agents` ordering, not completion order.
   - Sequential regression: ALL Phase 11 runner tests pass unchanged (this is the big gate).

7. **Rename `Phase 9: only sequential mode is implemented` error.** The `NotImplementedError` message at runner.py:168 should now say `"Phase 12: unknown mode {mode}. Known: sequential, parallel"` (and grow to include competitive/collaborative/debate/speculative as 12b-12e land).

**Files modified:** `src/ubongo/runner.py` (major restructure; sync wrapper + async strategies), `tests/test_runner.py` (+5 parallel + +1 unknown-mode message tweak).

### 12b — Competitive mode + Evaluator.rank

**Purpose:** N candidate agents get the same input; Evaluator picks a winner.

**Tasks:**

1. **`agents/evaluator.py`** — new `rank(...)` method:

   ```python
   def rank(
       self,
       message: str,
       candidates: list[tuple[str, str]],  # [(agent_name, text), ...]
       *,
       override_model: str | None = None,
   ) -> dict | None:
       """Pick the best candidate. Returns:
         {"winner": <agent_name>, "winner_index": int, "reason": str, "scores": [{...}]}
       or None on parse error / LLM failure.
       """
   ```

   - System prompt: borrows operator voice; explains task as comparative judging. Asks for JSON output:

     ```
     {
       "winner_index": <0-based int>,
       "reason": "<one short paragraph>",
       "scores": [{"index": 0, "score": 0.0..1.0, "note": "<one phrase>"}, ...]
     }
     ```

   - On parse error: return None (caller decides how to break the tie; runner falls back to "first candidate wins").
   - Honors `override_model` (Phase 11d Repair plumbing) and the existing `agents.evaluator.max_tokens` budget.
   - Token cost: bounded by max_tokens; large candidates are truncated to ~1KB each before insertion in the prompt.

2. **`_run_competitive`**:

   ```python
   async def _run_competitive(self, workflow, context, message, ...):
       # Convention: workflow.agents[:-1] are competitors; workflow.agents[-1] MUST be 'evaluator'.
       # Validate at run time; raise ValueError if not.
       evaluator_name = workflow.agents[-1]
       if evaluator_name != "evaluator":
           raise ValueError("competitive workflows must end with 'evaluator'")
       competitor_names = [n for n in workflow.agents[:-1] if n != "repair"]
       # Run all competitors in parallel.
       tasks = [self._dispatch_agent_async(...) for name in competitor_names]
       competitor_results = await asyncio.gather(*tasks)
       # Pick a winner via Evaluator.rank; persist evaluator's rank as an agent_runs row.
       ranking = await asyncio.to_thread(
           self.registry["evaluator"].rank,
           message,
           [(n, r.text) for n, r in zip(competitor_names, competitor_results) if r.ok],
       )
       # If ranking is None (parse error / no ok results), fall back to first ok competitor.
       ...
   ```

   - Persist the evaluator's ranking as a special `agent_runs` row: `agent="evaluator"`, `output={"winner": name, "reason": ...}`, `confidence` is the winner's score (so it still feeds governance via `WorkflowResult.evaluator_confidence`).
   - `WorkflowResult.text` = winning candidate's text. `WorkflowResult.model` = winning candidate's model.

3. **`workflows.yaml`** gets an example competitive workflow used by the smoke test (12f wires more):

   ```yaml
   coding_competitive:
     agents: ["coding", "architect", "evaluator"]   # coding vs architect on the same coding question
     mode: competitive
     evaluate: false   # competitive owns the evaluator already; do NOT auto-append a second one
   ```

4. **Phase 10 evaluate-flag interaction.** In competitive mode, the evaluator is the LAST entry in workflow.agents already. Master's plan-time auto-append (Phase 10d) would otherwise add a SECOND evaluator. Fix: `master.plan` skips the evaluator-append when `workflow.execution_mode == "competitive"` (the evaluator is required as part of the mode contract, not optional).

5. Tests in `tests/test_agents_evaluator.py` (+4 rank tests) and `tests/test_runner.py` (+3 competitive tests):
   - `rank` happy path: mocked LLM returns valid JSON; returns `{winner: "coding", winner_index: 0, ...}`.
   - `rank` parse error: returns None.
   - `rank` truncates large candidates.
   - `_run_competitive` validates last agent is evaluator.
   - `_run_competitive` calls evaluator.rank with all candidate texts; winner's text becomes WorkflowResult.text.
   - `_run_competitive` falls back to first-ok candidate when rank returns None.

**Files modified:** `src/ubongo/agents/evaluator.py` (+`rank`), `src/ubongo/runner.py` (+`_run_competitive`), `src/ubongo/master.py` (skip evaluate-append for competitive), `config/workflows.yaml` (+`coding_competitive`), `tests/test_agents_evaluator.py`, `tests/test_runner.py`.

### 12c — Collaborative mode

**Purpose:** Per-agent subtask via specialization; structural merge.

**Tasks:**

1. **`_run_collaborative`**:

   ```python
   async def _run_collaborative(self, workflow, context, message, ...):
       producer_names = [n for n in workflow.agents if n not in ("repair", "evaluator")]
       tasks = [self._dispatch_agent_async(...) for name in producer_names]
       results = await asyncio.gather(*tasks)
       # Structural merge: one section per agent, ordered by workflow.agents
       sections = []
       for name, res in zip(producer_names, results):
           if not res.ok:
               continue
           agent = self.registry[name]
           section_title = f"## {agent.role}"
           sections.append(f"{section_title}\n\n{res.text}")
       merged = "\n\n".join(sections) if sections else LLM_FAILURE_MESSAGE
       # If evaluate=true (master appended evaluator), run it AFTER merge to score the brief.
       ...
       return WorkflowResult(text=merged, ok=any_ok, ...)
   ```

   - Each agent sees the same input (no `prior_findings`, similar to parallel).
   - Specialization comes from each agent's existing system prompt — Research synthesizes facts, Critic challenges, etc.
   - The merged text is itself NOT a composer-attribute output; we mark `WorkflowResult.text = merged` directly. (No agent has `composer=True` in collab in the typical case; the runner short-circuits the last-composer rule for this strategy.)
   - The Phase-10 evaluator-append still works: if `evaluate: true`, the runner runs the evaluator AS A POST-MERGE STEP (not in parallel). Sequential after the parallel section.

2. **Why no new model/agent.** Collaborative mode is a runner strategy + the existing agents' specialization. No code changes to any agent class.

3. **Example `brief_collaborative` workflow** (added in 12f).

4. Tests in `tests/test_runner.py` (+3):
   - Collaborative: 2 agents both succeed; merged text contains both sections under their role headings, ordered.
   - Collaborative: one agent fails; merged text only has the surviving section.
   - Collaborative: all fail; `WorkflowResult.ok=False`, `text=LLM_FAILURE_MESSAGE`.

**Files modified:** `src/ubongo/runner.py` (+`_run_collaborative`), `tests/test_runner.py`.

### 12d — Debate mode

**Purpose:** Two debaters argue N rounds; synthesizer summarizes.

**Tasks:**

1. **`_run_debate`** (sequential, NOT parallel — debate is inherently turn-based):

   ```python
   async def _run_debate(self, workflow, context, message, ...):
       # Convention: workflow.agents = [debater_a, debater_b, synthesizer].
       # workflow.rounds (optional, default 2): number of debate rounds; each round = both speak.
       if len(workflow.agents) < 3:
           raise ValueError("debate workflows need [debater_a, debater_b, synthesizer]")
       debater_a, debater_b, *rest, synthesizer = workflow.agents
       rounds = getattr(workflow, "rounds", 2)
       transcript: list[tuple[str, str]] = []  # [(speaker, text), ...]
       for round_no in range(rounds):
           for speaker in (debater_a, debater_b):
               prior = [f"## Round {i+1} {sp}\n\n{txt}" for i, (sp, txt) in enumerate(transcript)]
               result = await self._dispatch_agent_async(
                   agent=self.registry[speaker], agent_name=speaker,
                   prior_findings=prior, ...,
               )
               if not result.ok:
                   break  # short-circuit on debate failure
               transcript.append((speaker, result.text))
       # Synthesizer sees the full transcript.
       synthesizer_prior = [f"## {sp}\n\n{txt}" for sp, txt in transcript]
       syn_result = await self._dispatch_agent_async(
           agent=self.registry[synthesizer], agent_name=synthesizer,
           prior_findings=synthesizer_prior, ...,
       )
       return WorkflowResult(text=syn_result.text, ok=syn_result.ok, ...)
   ```

   - Each debater sees the FULL prior transcript via `prior_findings`. The system prompt for the second-and-onward turns includes a "you are arguing against the prior position" frame — added via a new agent-side metadata flag `metadata["debate_role"] = "challenge"`. Phase 12 v0.1: Persona Agents read `debate_role` and append a one-line system-prompt stanza when set. **Open question 2.**
   - `rounds` is read from the workflow object; add a new optional `rounds: int | None = None` field on `Workflow` dataclass.
   - Synthesizer is the LAST entry; in practice `architect` (or `critic`).
   - Per-debater agent_runs rows are persisted with their own `started_at`/`ended_at`. The trace shows e.g. `architect`, `casual`, `architect`, `casual`, `architect` (for 2 rounds + synthesis = 5 rows).

2. **Example `debate_then_synthesize` workflow** (already exists; 12f updates it):

   ```yaml
   debate_then_synthesize:
     agents: ["architect", "operator", "architect"]   # A: architect, B: operator, synth: architect
     mode: debate
     rounds: 2
     evaluate: false
   ```

3. Tests in `tests/test_runner.py` (+3):
   - Debate 2 rounds: 5 agent_runs rows (A, B, A, B, synth), synth's text is WorkflowResult.text.
   - Debate failure mid-round: short-circuits; synth still runs on whatever transcript exists.
   - Debate rounds=1: 3 rows (A, B, synth).

**Files modified:** `src/ubongo/runner.py` (+`_run_debate`), `src/ubongo/master.py` (Workflow gains `rounds: int | None = None`), `src/ubongo/agents/personas.py` (read `debate_role` from metadata), `config/workflows.yaml` (update `debate_then_synthesize`), `tests/test_runner.py`.

### 12e — Speculative mode

**Purpose:** Cheap-first response; strong validates; correction concat on disagreement.

**Tasks:**

1. **`_run_speculative`** (in-turn, with a hard timeout):

   ```python
   async def _run_speculative(self, workflow, context, message, ...):
       # Convention: workflow.agents = [cheap, strong, evaluator].
       # workflow.timeout_s (optional, default 10).
       cheap_name, strong_name, *_ = workflow.agents
       evaluator_name = workflow.agents[-1] if workflow.agents[-1] == "evaluator" else None
       timeout_s = getattr(workflow, "timeout_s", 10)

       cheap_task = asyncio.create_task(self._dispatch_agent_async(
           agent=self.registry[cheap_name], agent_name=cheap_name, ...,
       ))
       strong_task = asyncio.create_task(self._dispatch_agent_async(
           agent=self.registry[strong_name], agent_name=strong_name, ...,
       ))

       try:
           done, pending = await asyncio.wait(
               {cheap_task, strong_task}, timeout=timeout_s,
               return_when=asyncio.ALL_COMPLETED,
           )
       except asyncio.CancelledError:
           ...

       cheap_result = cheap_task.result() if cheap_task.done() else None
       strong_result = strong_task.result() if strong_task.done() else None

       # Pick base text: prefer cheap (the speculative payoff is fast cheap response).
       base = cheap_result if (cheap_result and cheap_result.ok) else strong_result
       if base is None:
           return WorkflowResult(text=LLM_FAILURE_MESSAGE, ok=False, ...)

       # Validation: only if BOTH ran ok AND we have an evaluator.
       text = base.text
       if (cheap_result and strong_result and cheap_result.ok and strong_result.ok
           and evaluator_name and base is cheap_result):
           agree = await asyncio.to_thread(
               self.registry["evaluator"].agree,    # new method (12e)
               message, cheap_result.text, strong_result.text,
           )
           if agree is False:
               text = (
                   f"{cheap_result.text}\n\n---\n"
                   f"[Correction (slower model):]\n\n{strong_result.text}"
               )
       return WorkflowResult(text=text, ok=base.ok, ...)
   ```

2. **`EvaluatorAgent.agree(...)`** — new method:

   ```python
   def agree(self, message: str, text_a: str, text_b: str) -> bool | None:
       """Returns True if the two texts agree on the substantive answer to `message`,
       False if they materially disagree, None on parse error.

       JSON shape: {"agree": <bool>, "reason": "<one phrase>"}
       """
   ```

   - System prompt: short, focused on substantive disagreement (different facts, different recommendations). Phrasing differences don't count.
   - Honors `override_model`.

3. **`Workflow` dataclass** gains `timeout_s: int | None = None` (in addition to `rounds` from 12d).

4. **Example `speculative_brief` workflow** (12f updates):

   ```yaml
   speculative_brief:
     agents: ["casual", "architect", "evaluator"]   # cheap=casual, strong=architect, judge=evaluator
     mode: speculative
     timeout_s: 10
     evaluate: false  # the evaluator is part of the mode contract, not Phase-10 auto-append
   ```

5. **Decision: in-turn, NOT background.** True background (cheap returns instantly to user, strong runs after `master.handle` returns and enqueues a correction message) requires either an asyncio event loop running in the REPL (re-architecture) or a "drain pending tasks on next turn" hack. v0.1 ships in-turn: both run concurrently, total wall-time bounded by `timeout_s`, correction is appended to the same response message. Phase 13 may add the cross-turn variant when Repair Agent gets activation logic. **Open question 6.**

6. Tests in `tests/test_runner.py` (+5) and `tests/test_agents_evaluator.py` (+2):
   - Speculative both ok agree=true: text = cheap's text only.
   - Speculative both ok agree=false: text = cheap + "---" + correction.
   - Speculative cheap ok, strong fails: text = cheap; no correction.
   - Speculative cheap fails, strong ok: text = strong; no correction (since base != cheap).
   - Speculative both fail: WorkflowResult.ok=False.
   - `EvaluatorAgent.agree` happy path + parse error.

**Files modified:** `src/ubongo/runner.py` (+`_run_speculative`), `src/ubongo/agents/evaluator.py` (+`agree`), `src/ubongo/master.py` (`Workflow.timeout_s`), `config/workflows.yaml` (update `speculative_brief`), `tests/test_runner.py`, `tests/test_agents_evaluator.py`.

### 12f — workflows.yaml mode declarations (example workflows)

**Purpose:** Wire each new mode into a concrete example workflow. Most were stubbed in earlier sub-phases; 12f is the consolidation pass + router validation.

**Tasks:**

1. **Final `config/workflows.yaml`** (additions/changes consolidated):

   ```yaml
   workflows:
     # ... Phase 10/11 sequential workflows unchanged ...

     research_brief_parallel:
       agents: ["research", "architect"]
       mode: parallel
       evaluate: false   # parallel mode does not auto-append evaluator (12a)
     coding_competitive:
       agents: ["coding", "architect", "evaluator"]
       mode: competitive
       evaluate: false
     brief_collaborative:
       agents: ["research", "critic", "architect"]
       mode: collaborative
       evaluate: true    # post-merge sequential evaluator
     debate_then_synthesize:
       agents: ["architect", "operator", "architect"]
       mode: debate
       rounds: 2
       evaluate: false
     speculative_brief:
       agents: ["casual", "architect", "evaluator"]
       mode: speculative
       timeout_s: 10
       evaluate: false
   ```

2. **Router validation.** `router.workflow_mode(name)` returns the mode string today; that's enough. Add a constant `KNOWN_MODES = ("sequential", "parallel", "competitive", "collaborative", "debate", "speculative")` and validate workflow.execution_mode against it at workflow-load time (logs warning, falls through to `sequential` default).

3. **`router.workflow_rounds(name)`** + **`router.workflow_timeout_s(name)`** new helpers reading the optional fields.

4. Tests in `tests/test_router.py` (+5):
   - Each new workflow: `workflow_agents` and `workflow_mode` return the expected values.
   - `workflow_rounds("debate_then_synthesize") == 2`.
   - `workflow_timeout_s("speculative_brief") == 10`.
   - Unknown mode in workflows.yaml falls back to sequential with a warning.

**Files modified:** `config/workflows.yaml`, `src/ubongo/router.py` (`KNOWN_MODES`, `workflow_rounds`, `workflow_timeout_s`), `tests/test_router.py`.

### 12g — `/mode <workflow>` REPL command

**Purpose:** Operator-visible workflow override for the next turn. Mirrors `/skill <name>`'s pending-pattern.

**Tasks:**

1. **`Context` dataclass** gains `pending_workflow: str | None = None`. `master.handle` accepts a new `pending_workflow=None` kwarg.

2. **`master.plan`** changes: if `ctx.pending_workflow` is set AND it's a valid workflow name, use that workflow regardless of routing/persona. The pending workflow's `mode`, `agents`, and `evaluate` flag are honored verbatim. The persona becomes whatever the workflow's persona resolution returns (today's `router.workflow_persona(name)`).

3. **`repl.py`** additions:

   ```python
   def _parse_mode_command(line: str) -> str | None:
       """Returns the workflow name from `/mode <name>` or "list" sentinel for `/mode list`."""

   def _render_mode_list() -> str:
       """List available workflows + their modes."""

   # Slash dispatch:
   if head == "mode":
       requested = _parse_mode_command(stripped)
       if requested == "__list__":
           print(_render_mode_list())
       elif requested is None:
           print(f"Usage: /mode <workflow_name> | /mode list. {_HELP_COMMANDS}")
       elif requested not in <known workflows>:
           print(f"Unknown workflow: {requested}.")
       else:
           pending_workflow = requested
           print(f"Next turn will use workflow: {requested}.")
       continue
   ```

4. **`_HELP_COMMANDS`** updated to include `/mode <workflow>`.

5. **One-shot CLI** does NOT get a `--mode` flag in Phase 12 (per non-goals).

6. Tests in `tests/test_repl_mode.py` (~5):
   - `_parse_mode_command("/mode coding_competitive")` returns `"coding_competitive"`.
   - `_parse_mode_command("/mode list")` returns `"__list__"`.
   - `_parse_mode_command("/mode")` returns None.
   - `_render_mode_list()` includes all workflows from `workflows.yaml`.
   - End-to-end via mocked master: `/mode coding_competitive`, then a turn → `pending_workflow="coding_competitive"` passed to master.handle → workflow.execution_mode=="competitive" in the resulting WorkflowResult.

**Files modified:** `src/ubongo/repl.py`, `src/ubongo/master.py` (`Context.pending_workflow`, `plan` honors it), `tests/test_repl_mode.py` (new).

### 12h — STATUS + smoke playbook Phase 12 section

**Tasks:**

1. Append Phase 12 section to `tests/manual/smoke_test.md` with these scenarios:

   | # | Scenario | Steps | Expected |
   | --- | --- | --- | --- |
   | 12.1 | Sequential regression | Any technical question via `/architect` | Same as Phase 10 baseline; agent_runs in order; mode=sequential. |
   | 12.2 | Parallel via `/mode` | `rm -f data/ubongo.db`; in REPL: `/mode research_brief_parallel`; `compare Postgres vs DynamoDB for an event store` | Response composed by architect; `agent_runs` shows `research` + `architect` with overlapping `started_at` / `ended_at` (parallel); `workflow.agents == ["research","architect"]`; `mode == "parallel"`. |
   | 12.3 | Competitive via `/mode` | `/mode coding_competitive`; `write a Python function that reverses a list` | Two competitor `agent_runs` rows (`coding`, `architect`); one `evaluator` row with `confidence` + `output.winner` populated; `WorkflowResult.text` matches the winner's text. |
   | 12.4 | Collaborative via `/mode` | `/mode brief_collaborative`; `give me a brief on adopting microservices` | Response is a structured document with `## retrieval and synthesis ...`, `## contrarian challenger ...`, `## persona composer` headings. `agent_runs` shows research, critic, architect (parallel), then evaluator (sequential). |
   | 12.5 | Debate via `/mode` | `/mode debate_then_synthesize`; `should we use microservices for a 5-engineer team` | 5 agent_runs rows: architect, operator, architect, operator, architect (synthesizer). Synthesizer's text is the response; reads as a synthesis ("no, with caveats" or similar). |
   | 12.6 | Speculative agree | `/mode speculative_brief`; `what is the capital of France` | Response is the cheap (casual) reply; no `[Correction]` block. agent_runs shows casual, architect, evaluator (agreement check). Total wall time near max(cheap, strong). |
   | 12.7 | Speculative disagree | `/mode speculative_brief`; ask a deliberately ambiguous question where the cheap and strong models would diverge | Response begins with cheap text, then `---`, then `[Correction (slower model):]` block with strong's text. |
   | 12.8 | `/mode list` | `/mode list` | Lists all workflows from `workflows.yaml` with their modes. |
   | 12.9 | `/mode unknown` | `/mode phantom` | `Unknown workflow: phantom.` REPL state unchanged. |
   | 12.10 | `/mode` is one-shot | `/mode brief_collaborative`, then any turn, then another turn | Second turn does NOT use brief_collaborative (back to routed default). |
   | 12.11 | Unknown mode in workflows.yaml falls back | Edit a workflow's mode to `phantom`; restart; ask any question | Falls back to sequential; warning logged; response still works. Restore the file. |
   | 12.12 | Help line includes `/mode` | `/foo` | Help banner lists `/mode <workflow>`. |
   | 12.13 | Pytest passes | `uv run pytest tests/` | All green (~360 expected after Phase 12: Phase-11's 326 + 5 parallel + 7 competitive + 3 collaborative + 3 debate + 7 speculative + 5 router + 5 /mode). |

2. Update scenario 1.7 help-line expected text: add `/mode <workflow>` between `/exec <cmd>` and `/reload`.

3. Update `STATUS.md`: Phase 12 row → Awaiting merge; Overall paragraph updated; LOC bumped; mark "End of Tier 2 (Multi-Agent System)".

**Files modified:** `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 12

```text
src/ubongo/
  runner.py                            (major: sync wrapper + async strategies for all 6 modes)
  master.py                            (modified: Workflow.rounds + .timeout_s; Context.pending_workflow;
                                                  plan honors pending_workflow; skip evaluate-append for competitive)
  repl.py                              (modified: /mode command + help line)
  router.py                            (modified: +KNOWN_MODES, +workflow_rounds, +workflow_timeout_s)
  agents/
    evaluator.py                       (modified: +rank, +agree)
    personas.py                        (modified: read debate_role from metadata)
config/
  workflows.yaml                       (modified: add 5 example workflows for new modes)
tests/
  test_runner.py                       (modified: +5 parallel + 3 competitive + 3 collaborative
                                                  + 3 debate + 5 speculative)
  test_agents_evaluator.py             (modified: +4 rank + 2 agree)
  test_router.py                       (modified: +5 mode/workflow tests)
  test_repl_mode.py                    (new ~5)
Plans/
  phase-12-modes.md                    (new — this file)
STATUS.md                              (modified)
tests/manual/smoke_test.md             (modified — Phase 12 section + 1.7 help-line tweak)
```

Untouched: `classifier.py`, `delivery/queue.py`, `memory/*`, `agents/memory.py` / `research.py` / `critic.py` / `coding.py` / `execution.py` / `repair.py` / `base.py`, `governance/decision.py`, `oneshot.py`, `events.py`, `sandbox.py`, `config/personas/*`, `config/skills/*`, `config/UBONGO.md`, `config/routing.yaml` (Phase 12 doesn't auto-route to the new modes; users opt in via `/mode`).

## Out of scope for Phase 12 (do NOT build)

- `--mode <workflow>` CLI flag for one-shot. REPL only.
- True cross-turn background speculative (cheap returns immediately, strong runs in a real background loop, correction enqueued for next turn). v0.1 ships in-turn with timeout.
- Auto-routing classifier suggestions for the new modes. The Master still picks workflows from `routing.yaml`; new workflows are reachable only via `/mode` in Phase 12.
- Per-mode token budget caps. The existing per-agent `max_tokens` apply; total parallel/competitive/debate cost is roughly `sum(per-agent)`. Phase 14 may add a cap.
- Repair retry in non-sequential modes. Sequential is the only mode where the runner consults `RepairAgent.plan_retry`. Failures in parallel/competitive/collaborative/debate/speculative are reported but not retried; Phase 13 may revisit.
- Multi-debater (3+ sides) or per-round role swapping. Two-sided debate only.
- Recording the EvaluatorAgent.rank ranking JSON in a structured way (e.g., one row per scored candidate). Phase 12 stores the JSON in the existing `agent_runs.output` column.
- `composer = True` mid-loop changes. The runner's last-composer-wins rule applies in sequential and parallel; collaborative/competitive/debate/speculative override the text source explicitly (per-strategy decision).

## Open questions to confirm before I start

1. **Parallel mode skips the auto-appended evaluator (recommended).** When `evaluate: true` is set on a parallel workflow, the evaluator would otherwise run in parallel with the persona (which is wrong — it can't score what hasn't finished). Phase 12: master.plan does NOT auto-append evaluator for parallel-mode workflows. If you want post-parallel evaluation, declare a sequential workflow that wraps the parallel one (out of scope for Phase 12) or use competitive mode (which has the evaluator built in). Alternative: silently demote parallel to "parallel-then-evaluator-sequential" — confusing. I lean skip-with-config-warning. OK?

2. **`debate_role` metadata flag for second-and-onward debater turns (recommended).** When B speaks in round 1, B sees A's text in `prior_findings`; we want B's system prompt to include "you are arguing against the prior position." Adding a one-liner system-prompt stanza when `input.metadata["debate_role"] == "challenge"` is set keeps the agents themselves dumb. Alternative: a separate `DebateAgent` wrapper class — overkill. I lean metadata. OK?

3. **`Workflow` dataclass gains optional `rounds` and `timeout_s` (recommended).** Both are mode-specific config. Putting them on `Workflow` means `asdict(workflow)` carries them into `workflow_runs.workflow` JSON for the trace. Alternative: dict-typed `mode_config: dict | None` field — more flexible but stringier. I lean explicit fields for the v0.1 set. OK?

4. **`/mode` is one-shot (clears after the next turn) (recommended).** Mirrors `/skill <name>`'s pattern. Alternative: sticky until `/mode auto` clears it. I lean one-shot — easier to reason about; explicit re-pin per turn. OK?

5. **Competitive workflow contract: last agent MUST be `evaluator` (recommended).** The runner validates this at run time. Alternative: a separate `competitors:` field in workflows.yaml. I lean convention-based — keeps workflows.yaml shape consistent across modes. OK?

6. **Speculative is in-turn with hard timeout, NOT cross-turn background (recommended).** v0.1 trades the "true background" promise for simplicity. The user still gets cheap-fast (cheap finishes first, returned as the leader) and a correction (if strong disagrees within timeout). Alternative: build a runner-level pending-tasks list flushed on next master.handle call; spec text says "follow-up correction within ~10s" which the in-turn version satisfies. I lean in-turn for v0.1; revisit Phase 13 if Repair needs background infra anyway. OK?

7. **Repair retry only fires in sequential mode (recommended).** Cancel-and-retry semantics in fan-out modes are ambiguous; Phase 11's single-retry is built around "the next agent is waiting for me to succeed." Phase 12 documents this and leaves Repair off in the other 5 modes. Alternative: retry one failing parallel/competitive task in-place after gather completes — doable but complicated. I lean off-in-fan-out. OK?

8. **`EvaluatorAgent` gains TWO new methods (`rank` + `agree`) rather than one merged `judge`-with-mode (recommended).** Each has a distinct prompt + response shape; merging them with a flag would push branching into both call site and the prompt. I lean separate methods. OK?

9. **Collaborative-mode evaluator runs sequentially AFTER the merged document (recommended).** When `evaluate: true` is set on a collaborative workflow, the evaluator sees the merged brief and scores it. (Parallel mode's evaluator would have nothing to score; collaborative's merged output IS a thing to score.) OK?

10. **No `oneshot --mode` CLI flag in Phase 12 (recommended).** REPL `/mode` only. Adding a `--mode` flag means parsing + validation + tests in `oneshot.py`. v0.1 one-shot users keep the routed default; if they need a specific mode they use REPL. OK?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 12

- 9 commits on `phase-12-modes` (Plan + 12a–12h). Push branch + open draft PR immediately after the Plan commit.
- Smoke scenarios 12.1–12.13 pass; 12.13 pytest green.
- New tests: `test_repl_mode.py` (~5). Existing tests in `test_runner.py` (+19 across modes), `test_agents_evaluator.py` (+6), `test_router.py` (+5).
- `tests/manual/smoke_test.md` Phase 12 section appended; help-line tweak in scenario 1.7.
- `STATUS.md` Phase 12 row → Complete; "Overall" paragraph refreshed; LOC count bumped; "End of Tier 2 (Multi-Agent System)" called out.
- Branch handed to you for merge. **Don't merge.**

---

(Verified: `origin/main` matches local `main` at `1f023d2`. Phases 0-11 fully merged. Branch `phase-12-modes` exists locally; not yet pushed.)
