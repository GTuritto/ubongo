# Phase 13 — Repair Agent Activated: Implementation Plan

Date: 2026-05-15
Branch: `phase-13-repair` (off `main` at `4bf6c0f`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 13 (lines 1062–1093), Worker Agents table (105–129) Repair row, `agent_failed` event (317), Tier-3 narrative (1062–1095). Smoke walkthrough on `main` 2026-05-15 (`382/382 pytest green`) surfaced two natural Phase-13 inputs: `critic_no_candidate` in collaborative mode (parallel producers leave Critic with no candidate to critique) and `evaluator_parse_error` (LLM judge returned non-JSON on a real prompt). Phase 13 turns both into recoveries.

## Goal

Real failure detection plus multi-step recovery. Phase 11 shipped a single-retry-with-model-fallback fallback path; Phase 13 turns the Repair Agent into the real failure surface:

1. **Failure taxonomy.** Categorize agent failures into `timeout | model_error | parse_error | content_rejection | precondition_missing | infinite_loop`. The agent error codes already in the codebase (`*_llm_error`, `evaluator_parse_error`, `critic_no_candidate`, `execution_refused`, etc.) map to taxonomy kinds; `RepairAgent` reads the kind, not the raw error string, when picking a strategy. `precondition_missing` is the "input contract wasn't met" bucket (`critic_no_candidate`, `memory_missing_input`, `execution_no_command`); its ladder skips variant-prompt retries because re-prompting the same agent with no candidate to critique is futile — only peer replacement or abort makes sense.
2. **Multi-strategy retry.** `RepairAgent.plan_recovery` returns an *ordered list* of strategy plans, not a single `{model}` dict. The runner walks the list until one succeeds or it is exhausted: (a) same model + different prompt (stricter schema for parse errors; clearer instruction for content refusals), (b) different model same prompt (current Phase-11 behavior; keep for model-specific bugs), (c) smaller model + shorter prompt (cost-bounded last resort), (d) peer-agent replacement when configured, (e) abort + apologize.
3. **Agent replacement** declared in `settings.yaml::agents.repair.peer_replacements`. Example: `critic: architect` means a `critic_no_candidate` (or any unrecoverable critic failure) falls back to the architect persona running in the critic's slot. Defaults are conservative (no peer for `evaluator` or `memory`; `critic -> architect`; `coding -> architect`; `research -> architect`).
4. **Workflow rollback (write-buffer pattern, v0.1 scope).** The persisted-message and vault-projection writes that today already happen post-success (`master.handle` line 344+, `vault._after_send_handler`) are routed through a single `WriteBuffer` context. The buffer collects intent-to-write entries during execute and commits on workflow success / drops on workflow failure. Today the only mid-flight writers Phase 13 touches are the assistant-message commit and `after_send` vault projection; the seam exists so Phase 19/20 agents can stage further writes (e.g., research-discovered facts) without each agent reinventing rollback.
5. **`repair_runs` audit table.** Each repair attempt persists one row (FK → `workflow_runs.id`, FK-loose ref → the failing `agent_runs.id`): failure_kind, original_error, strategy_attempted, peer_agent, attempt_index, outcome, timings. `/trace` renders the chain under the affected agent_run row.
6. **Unrecoverable user-visible behavior.** When all strategies are exhausted, master returns an apology and a `requires_user_decision` flag. REPL prints `Repair exhausted (N attempts). Retry from a fresh prompt? (y/n)` and routes y/n into a fresh turn / clean prompt. One-shot prints the apology and exits rc=1 (no prompt; the user can re-run the command).
7. **`workflow_runs.outcome='repaired'`** set when any repair fired *and* the final result was ok. Otherwise `success` (no repair) or `failure` (unrecoverable). The enum value already exists in the schema; Phase 13 lights it up.

**Non-goals for Phase 13 (locked):**
- Infinite-loop detection. The taxonomy declares the kind for forward-compat; the detector is a no-op v0.1 because the runner uses a fixed agent list per workflow (there is no agent-to-agent invocation loop to detect yet). Lit up when Phase 18 GP introduces variant-driven dynamic routing.
- Full multi-strategy retry across **every** execution mode. Sequential mode gets the full strategy list. Fan-out modes (parallel, competitive, collaborative, debate, speculative) get **peer-replacement only** — cancel-and-retry semantics in `asyncio.gather` are still ambiguous (the runner comment from Phase 12 explicitly defers this). Peer-replacement is enough to fix 12.4's `critic_no_candidate` and similar drop-out failures.
- Approval prompts before each repair attempt. Per-retry governance gating is Phase 14/15 scope (`governance/approval.py`). Phase 13 just does the work and surfaces cost via `/trace`.
- Real distributed-write staging. `WriteBuffer` is minimal v0.1 — it covers the assistant-message commit and the vault projection. Buffered writes for hypothetical Phase-19/20 agent-staged facts can live behind the same interface later; nothing changes the public seam.
- A REPL `/repair` command. The audit table is read via `/trace` (which grows a repair line). A dedicated command can land in Phase 14/15 if useful.
- Live model-cost / token-budget tracking across strategies. Repair counts strategy attempts; an explicit hourly call cap lives in `evolution` config and Phase 17 will lean on it. Phase 13 stays under a per-workflow attempt cap (default 3 strategies tried max; configurable via `agents.repair.max_attempts`).

## Why this plan exists

Three patterns Phase 13 locks in that Phases 14–19 inherit:

1. **The Repair Agent owns recovery policy; the runner owns recovery mechanism.** Today the runner has a hard-coded one-retry-with-model-fallback loop in `_run_sequential`. Phase 13 inverts that: `RepairAgent.plan_recovery(failure, agent_name, attempt_count)` returns a strategy descriptor (`{strategy: "retry_same_model_different_prompt", prompt_variant: "..." | None, override_model: "..." | None, peer_agent: "..." | None}`); the runner executes the strategy and asks Repair for the next one if it fails. This is the same shape Phase 18/19's GP loop will reuse when it asks the variant generator for "the next thing to try". The runner's failure path becomes a thin loop over Repair's plan list, not branching policy code.
2. **Write-side effects are gated through a single buffer.** Today the only mid-flight writes are commits the Memory Agent makes from `master.handle` after execute returns ok. That keeps state clean *by accident* — the architecture relies on "execute returns first, then commit". Phase 13 makes it explicit by routing `commit_assistant_turn` and `vault._after_send_handler` through `memory.WriteBuffer`. The buffer interface is intentionally small (`stage(callable)` + `commit()` + `drop()`) so Phase 19/20 agents can stage further writes without each one inventing rollback. CLAUDE.md "MemoryAgent is the only writer" stays true; the buffer is the seam that enforces "only on workflow success".
3. **Trace honesty over recovery cleverness.** Every retry attempt writes one `repair_runs` row AND a fresh `agent_runs` row (with `retried=True` and a new `repair_attempt_index` column). When the user runs `/trace`, they see exactly what failed, what was tried, in what order, and what landed. There is no hidden recovery path — including peer-replacement, which records a `replace_with_peer` strategy row and a normal `agent_runs` row for the peer running in the failed slot. Phase 14's governance audit and Phase 18's GP fitness signal both depend on this trace fidelity.

## Branch + commit strategy

Branch: `phase-13-repair` off `main` at `4bf6c0f` (HEAD; Phase 12 + smoke walkthrough merged).

Per `feedback_phase_branch_open_draft_pr.md`: push the branch and open a **draft PR** immediately after this plan commit, base `main`. PR title: `Phase 13 — Repair Agent Activated`. PR stays draft until 13g lands and the smoke playbook Phase-13 section is green.

Eight commits matching the spec's sub-phase letters + one for STATUS:

- **13a — Failure taxonomy.** `agents/repair.py` gets a `FailureKind` enum + `_classify_failure(agent_name, error_code) -> FailureKind` mapping. `_RETRYABLE_ERRORS` retires; the policy now lives in per-kind strategy lists. Tests.
- **13b — Multi-strategy retry.** `RepairAgent.plan_recovery(failure_kind, agent_name, attempt_index)` returns `RecoveryPlan | None`. Strategy enum + per-kind ordering. `runner._run_sequential` loops Repair plans until one succeeds or returns None. Tests.
- **13c — Peer replacement.** `agents.repair.peer_replacements` in `settings.yaml`. `RepairAgent` returns `RecoveryPlan(strategy="replace_with_peer", peer_agent=...)` as the last strategy when one is configured. Runner dispatches the peer in the failed agent's slot. Tests. Smoke 12.4 collaborative critic_no_candidate gets fixed here.
- **13d — Write-buffer pattern.** `memory/write_buffer.py`: `WriteBuffer` context object. `master.handle` opens one per turn; commit on result.ok, drop on failure. Routes `default_memory_agent.commit_assistant_turn` and the `after_send` vault projection through the buffer. Tests (`test_memory_write_buffer.py`).
- **13e — `repair_runs` audit table.** Schema change + migration shim. `store.append_repair_run(...)` writer. `runner._handle_agent_failure` writes one per strategy attempted (success or fail). `/trace` renderer grows a `repair:` line under each affected agent_run. Tests.
- **13f — Unrecoverable apology + y/n.** `master.WorkflowResult` gains `requires_user_decision: bool` and `repair_summary: dict | None`. `repl._handle_unrecoverable` prints the apology + prompts `y/n`. `y` re-issues the prior user turn (a single fresh attempt; if it fails again, normal apology + rc-style return). `n` returns to a clean prompt. One-shot prints the apology and exits rc=1.
- **13g — STATUS + smoke playbook Phase 13 section.** Append Phase-13 scenarios to `tests/manual/smoke_test.md`. Update `STATUS.md` row and overall paragraph. Patch the two stale playbook items the 2026-05-15 walkthrough flagged (2.5 attempt count and 11.9 LIKE query) at the same time.

## Sub-phases

### 13a — Failure taxonomy (`src/ubongo/agents/repair.py`)

**Purpose:** Decouple "what kind of failure is this" from "which fallback model do we have for that agent". Phase 11's flat error-string set becomes a category map; the strategy chooser in 13b reads the kind, not the raw error.

**Tasks:**

1. Replace `_RETRYABLE_ERRORS` / `_NEVER_RETRY_AGENTS` in `agents/repair.py` with:

   ```python
   class FailureKind(str, Enum):
       TIMEOUT = "timeout"
       MODEL_ERROR = "model_error"
       PARSE_ERROR = "parse_error"
       CONTENT_REJECTION = "content_rejection"
       PRECONDITION_MISSING = "precondition_missing"   # input contract not met
       INFINITE_LOOP = "infinite_loop"     # declared; detector is no-op v0.1
       UNRECOVERABLE = "unrecoverable"     # explicit "do not repair"

   # Map agent error codes (the strings each agent sets on AgentResult.error)
   # to a failure kind. Codes not in the map fall through to UNRECOVERABLE.
   _ERROR_KIND: dict[str, FailureKind] = {
       # LLM errors (timeouts surface as model_error from litellm today;
       # Phase 14 may add a dedicated TimeoutError code).
       "persona_llm_error":    FailureKind.MODEL_ERROR,
       "research_llm_error":   FailureKind.MODEL_ERROR,
       "evaluator_llm_error":  FailureKind.MODEL_ERROR,
       "critic_llm_error":     FailureKind.MODEL_ERROR,
       "coding_llm_error":     FailureKind.MODEL_ERROR,
       # Parse errors — stricter-schema retry pays here.
       "evaluator_parse_error":      FailureKind.PARSE_ERROR,
       "evaluator_rank_parse_error": FailureKind.PARSE_ERROR,
       "evaluator_agree_parse_error": FailureKind.PARSE_ERROR,
       "classifier_parse_error":     FailureKind.PARSE_ERROR,
       # Precondition failures — input contract not met. Re-prompting the
       # same agent is futile; only peer replacement or abort makes sense.
       "critic_no_candidate":  FailureKind.PRECONDITION_MISSING,
       "memory_missing_input": FailureKind.PRECONDITION_MISSING,
       "execution_no_command": FailureKind.PRECONDITION_MISSING,
       # By-design refusals: do not retry.
       "execution_refused":    FailureKind.UNRECOVERABLE,
   }
   ```

2. `_classify_failure(agent_name: str, error_code: str | None) -> FailureKind`:

   - `agent_name == "memory"` AND `error_code != "memory_missing_input"` → `UNRECOVERABLE` (DB writes need Phase-21 rollback, not Phase-13 retry).
   - Look up `error_code` in `_ERROR_KIND`. Hit → return it.
   - Miss + `error_code is None` → `MODEL_ERROR` (the agent crashed without setting an error; treat like an LLM bug worth one retry-different-model).
   - Miss + non-None → `UNRECOVERABLE` (unknown error string; log and bail; do not invent a strategy).

3. Drop the Phase-11 `RepairAgent.plan_retry` API but keep a thin shim so the runner's old call site doesn't break while 13b lands. Mark with a `# Phase 11 shim; Phase 13b replaces with plan_recovery` comment.

4. Tests in `tests/test_agents_repair.py` (rewrite ~10 cases):

   - `_classify_failure("evaluator", "evaluator_parse_error")` → `PARSE_ERROR`.
   - `_classify_failure("critic", "critic_no_candidate")` → `PRECONDITION_MISSING`.
   - `_classify_failure("memory", "memory_missing_input")` → `PRECONDITION_MISSING`.
   - `_classify_failure("execution", "execution_no_command")` → `PRECONDITION_MISSING`.
   - `_classify_failure("execution", "execution_refused")` → `UNRECOVERABLE`.
   - `_classify_failure("memory", "any_other_code")` → `UNRECOVERABLE`.
   - `_classify_failure("research", None)` → `MODEL_ERROR` (agent crashed).
   - `_classify_failure("operator", "totally_unknown")` → `UNRECOVERABLE`.

**Files modified:** `src/ubongo/agents/repair.py`, `tests/test_agents_repair.py`.

### 13b — Multi-strategy retry (`src/ubongo/agents/repair.py` + `src/ubongo/runner.py`)

**Purpose:** Replace single-retry-with-model-fallback with an ordered strategy list per failure kind. The runner walks the list; each strategy is one fresh agent dispatch.

**Tasks:**

1. New dataclasses in `agents/repair.py`:

   ```python
   class RecoveryStrategy(str, Enum):
       RETRY_SAME_MODEL_VARIANT_PROMPT     = "retry_same_model_variant_prompt"
       RETRY_DIFFERENT_MODEL_SAME_PROMPT   = "retry_different_model_same_prompt"
       RETRY_SMALLER_MODEL_SHORTER_PROMPT  = "retry_smaller_model_shorter_prompt"
       REPLACE_WITH_PEER                   = "replace_with_peer"
       ABORT                               = "abort"

   @dataclass(frozen=True)
   class RecoveryPlan:
       strategy: RecoveryStrategy
       override_model: str | None = None      # for *_MODEL_* strategies
       prompt_hint: str | None = None         # for VARIANT_PROMPT / SHORTER_PROMPT
       peer_agent: str | None = None          # for REPLACE_WITH_PEER
       reason: str | None = None              # for ABORT (user-facing apology hint)
   ```

2. `RepairAgent.plan_recovery(failure_kind, agent_name, attempt_index) -> RecoveryPlan`:

   The per-kind strategy list:

   | Kind | Strategies in order |
   | --- | --- |
   | PARSE_ERROR | RETRY_SAME_MODEL_VARIANT_PROMPT (stricter schema hint) → REPLACE_WITH_PEER (if configured) → ABORT |
   | MODEL_ERROR | RETRY_DIFFERENT_MODEL_SAME_PROMPT (current Phase-11 behavior) → RETRY_SMALLER_MODEL_SHORTER_PROMPT → REPLACE_WITH_PEER → ABORT |
   | TIMEOUT | RETRY_SMALLER_MODEL_SHORTER_PROMPT → REPLACE_WITH_PEER → ABORT |
   | CONTENT_REJECTION | RETRY_SAME_MODEL_VARIANT_PROMPT (rephrase instruction) → REPLACE_WITH_PEER → ABORT |
   | PRECONDITION_MISSING | REPLACE_WITH_PEER (if configured) → ABORT (no retry of same agent — input contract won't be met by re-running) |
   | INFINITE_LOOP | ABORT (no v0.1 strategy; placeholder for Phase 18+) |
   | UNRECOVERABLE | ABORT |

   `attempt_index` (0-based) indexes into the strategy list; when it runs off the end, return `RecoveryPlan(strategy=ABORT, reason=<friendly>)`. The runner caps total attempts at `agents.repair.max_attempts` (default 3) regardless of list length.

3. Prompt hints (passed to the agent via `input.metadata["repair_prompt_hint"]`):

   - PARSE_ERROR variant: `"The previous attempt returned text that could not be parsed as JSON. Return ONLY a JSON object that matches the schema exactly; no prose, no fences."`
   - CONTENT_REJECTION variant: `"The previous attempt did not produce a response. Answer the user's question directly; if the question is genuinely unanswerable, say so in one sentence."`
   - SHORTER_PROMPT hint: `"Be concise. Answer in under 200 tokens."` (set alongside `override_model="...haiku..."`).

4. Each LLM-calling agent (`personas.py`, `coding.py`, `research.py`, `evaluator.py`, `critic.py`) gets a one-line read:

   ```python
   prompt_hint = input.metadata.get("repair_prompt_hint")
   if prompt_hint:
       sections.append("## Repair guidance\n\n" + prompt_hint)
   ```

   Append AFTER the role / instruction sections so the hint takes priority over default phrasing.

5. **Runner wiring** (`src/ubongo/runner.py::_run_sequential`):

   Replace the existing single-retry block with a `_recover_or_give_up` helper:

   ```python
   async def _recover_or_give_up(
       self,
       *,
       agent, agent_name, message, history, summary_text, prior_findings,
       workflow, context, workflow_run_id, failed_result,
   ) -> tuple[AgentResult, list[dict]]:
       """Return (final_result, repair_run_payloads_to_persist).

       Walks RepairAgent.plan_recovery(...) until a strategy succeeds or
       the strategy list / max_attempts is exhausted. Each strategy
       attempt produces one repair_runs row AND one agent_runs row
       (retried=True; repair_attempt_index=N).
       """
   ```

   The helper:
   - Asks `repair_agent.plan_recovery(failure_kind, agent_name, attempt_index=0)` → plan.
   - If `plan.strategy == ABORT`: return `(failed_result, [abort_row])`. Done.
   - If `plan.strategy == REPLACE_WITH_PEER`: dispatch `self.registry[plan.peer_agent]` with the same `AgentInput` (no override_model; no prompt_hint by default). Return its result.
   - Otherwise: dispatch the same `agent` with `override_model=plan.override_model` and `extra_metadata={"repair_prompt_hint": plan.prompt_hint}`. If ok, return. If not, increment `attempt_index` and ask Repair for the next plan; loop until ok or ABORT.
   - Cap iterations at `agents.repair.max_attempts` (default 3).

6. Settings: `agents.repair.max_attempts: 3` added to `settings.yaml` with a comment.

7. Tests in `tests/test_agents_repair.py` (~6 new):
   - `plan_recovery(PARSE_ERROR, "evaluator", attempt_index=0).strategy == RETRY_SAME_MODEL_VARIANT_PROMPT`.
   - `plan_recovery(PARSE_ERROR, "evaluator", attempt_index=1).strategy == REPLACE_WITH_PEER` (when peer configured) OR `ABORT` (no peer).
   - `plan_recovery(MODEL_ERROR, "coding", 0).strategy == RETRY_DIFFERENT_MODEL_SAME_PROMPT` with override_model from settings.
   - `plan_recovery(MODEL_ERROR, "coding", 2).strategy == REPLACE_WITH_PEER`.
   - `plan_recovery(UNRECOVERABLE, ...).strategy == ABORT`.
   - `plan_recovery(...attempt_index >= max_attempts).strategy == ABORT`.

8. Tests in `tests/test_runner.py` (~6 new):
   - `test_repair_parse_error_recovers_with_variant_prompt`: mock evaluator fails once with parse error, succeeds on the retry with `metadata.repair_prompt_hint` set.
   - `test_repair_model_error_walks_to_smaller_model`: same-model + different-model both fail; smaller-model succeeds.
   - `test_repair_exhausts_strategies`: every strategy fails; runner returns `WorkflowResult.ok=False` and `repair_summary` populated.
   - `test_repair_max_attempts_capped`: 5 strategies declared but `max_attempts=2`; only 2 attempts happen.
   - `test_repair_records_each_strategy_in_repair_runs`: after a 3-strategy chain, `repair_runs` has 3 rows tied to the same workflow_run_id.
   - `test_repair_unrecoverable_short_circuits`: agent fails with `execution_refused`; Repair returns ABORT immediately; only one agent_runs row (the original) — no retry attempts.

**Files added:** none.
**Files modified:** `src/ubongo/agents/repair.py` (rewrite plan_recovery; keep plan_retry shim until 13c lands), `src/ubongo/runner.py` (_recover_or_give_up helper; _run_sequential uses it; fan-out modes call a slimmed _replace_or_skip variant — see 13c), `src/ubongo/agents/personas.py` + `coding.py` + `research.py` + `evaluator.py` + `critic.py` (read `repair_prompt_hint` from metadata, one-line each), `config/settings.yaml` (+`agents.repair.max_attempts: 3`), `tests/test_agents_repair.py`, `tests/test_runner.py`.

### 13c — Peer replacement (`config/settings.yaml` + runner glue)

**Purpose:** Wire `agents.repair.peer_replacements` from settings into Repair's strategy chooser. Light up the fix for 12.4's `critic_no_candidate` (and any future drop-out failure) by substituting a peer agent in the failed slot.

**Tasks:**

1. `config/settings.yaml`:

   ```yaml
   agents:
     repair:
       max_attempts: 3
       fallback_models: {}            # Phase 11; unchanged
       peer_replacements:
         # Each entry maps a failing agent to a peer who can stand in for
         # one workflow turn. The peer runs in the failed agent's slot with
         # the same AgentInput (no prompt_hint by default). null disables.
         critic: architect            # 12.4 collab critic_no_candidate fix
         coding: architect
         research: architect
         evaluator: null              # no peer; LLM-as-judge is structurally unique
         memory: null                 # never replace memory
         execution: null              # sandbox refusals are by design
         architect: null              # personas are the user-visible voice
         operator: null
         casual: null
   ```

2. `RepairAgent.__init__` reads `peer_replacements`. `RepairAgent.peer_for(agent_name) -> str | None`. `plan_recovery` calls `peer_for` when it would otherwise emit `REPLACE_WITH_PEER`; if `None`, the strategy is skipped and the chooser advances to `ABORT`.

3. **Runner fan-out coverage.** Sequential is wired up by 13b. Phase 13c adds a slimmer `_replace_or_skip` path for parallel/competitive/collaborative/debate/speculative:

   - Each fan-out mode currently sets `any_failure = True` and continues when an agent fails (or short-circuits in debate). Phase 13 changes that to: on failure, ask `RepairAgent.plan_recovery(kind, agent_name, attempt_index=0)`; if the FIRST plan is `REPLACE_WITH_PEER`, dispatch the peer in the failed slot and use its result. Otherwise leave the existing behavior (continue with `any_failure=True`). Multi-strategy retry is sequential-only in v0.1.
   - Concretely: a `await self._maybe_replace_failed(...)` helper sits between `result = await self._dispatch_agent_async(...)` and the `any_failure = True` branch in `_run_parallel`, `_run_competitive`, `_run_collaborative`, and `_run_debate`. Speculative gets the same helper for the cheap-or-strong leader (the other side already serves as a natural fallback).
   - When the peer succeeds, the runner writes ONE `agent_runs` row for the peer (with `retried=True`) AND ONE `repair_runs` row (`strategy='replace_with_peer'`, `peer_agent='architect'`).

4. **Composer / role implications** in collaborative mode: the peer's role string is what shows up under `## <role>` in the merged document. Acceptable v0.1 — the user sees "## persona composer" where the critic section would have been, which is honest. Phase 14 may want a `displayed_as` override but it's not necessary now.

5. Tests in `tests/test_runner.py` (~3 new):
   - `test_collaborative_critic_no_candidate_replaced_with_peer`: critic fails with `critic_no_candidate`; runner substitutes architect; merged doc has architect's text under `## persona composer` heading; agent_runs shows critic (failure) and architect (success, retried=1); repair_runs row records `strategy='replace_with_peer'`, `peer_agent='architect'`.
   - `test_parallel_unrecoverable_does_not_replace`: agent fails with `execution_refused`; no peer dispatched; existing `any_failure=True` behavior preserved.
   - `test_speculative_cheap_fail_strong_ok_no_peer_invoked`: cheap fails, strong succeeds; Repair is NOT asked because speculative already has a natural fallback path; only one repair_runs row would be wasteful.

**Files modified:** `config/settings.yaml`, `src/ubongo/agents/repair.py`, `src/ubongo/runner.py` (each fan-out mode gets `_maybe_replace_failed`), `tests/test_runner.py`.

### 13d — Write-buffer pattern (`src/ubongo/memory/write_buffer.py` + master glue)

**Purpose:** Make the "only commit if the workflow succeeded" rule explicit. Today the assistant-message write happens after `execute` returns, gated by `result.ok`. Vault projection fires on `after_send`. Phase 13 routes both through a single buffer so the contract is enforced in one place (and forward-compatible with future agent-staged writes).

**Tasks:**

1. New `src/ubongo/memory/write_buffer.py`:

   ```python
   from contextlib import contextmanager
   from collections.abc import Callable

   class WriteBuffer:
       """A staging area for memory-side writes that should only land
       if the surrounding workflow succeeds.

       v0.1 covers two writers:
         - MemoryAgent.commit_assistant_turn (master.handle line 347)
         - vault._after_send_handler (registered as an after_send subscriber)

       The buffer is a single-turn object; master.handle constructs one
       per turn and either commits or drops it before returning.

       Phase 19/20 agents can stage further writes via stage(callable) when
       they need rollback semantics; v0.1 has no such callers.
       """
       def __init__(self) -> None:
           self._staged: list[Callable[[], None]] = []
           self._committed: bool = False

       def stage(self, write: Callable[[], None]) -> None:
           self._staged.append(write)

       def commit(self) -> None:
           if self._committed:
               raise RuntimeError("WriteBuffer already committed")
           for write in self._staged:
               write()
           self._committed = True

       def drop(self) -> None:
           # Caller decided the workflow failed; release without writing.
           self._staged.clear()
           self._committed = True


   @contextmanager
   def workflow_buffer():
       buf = WriteBuffer()
       try:
           yield buf
       finally:
           if not buf._committed:
               # Defensive: caller forgot to commit/drop. Drop wins.
               buf.drop()
   ```

2. `master.handle` opens the buffer:

   ```python
   with workflow_buffer() as buf:
       ...
       result = self.execute(...)
       ...
       if result.ok and not rejected:
           buf.stage(lambda: default_memory_agent.commit_assistant_turn(...))
           # vault projection still fires on after_send; the after_send
           # subscriber stages itself into the buffer via the queue token.
           buf.commit()
       else:
           buf.drop()
   ```

3. Vault projection refactor: today `MemoryAgent.project_vault` is registered as a permanent `after_send` subscriber and runs unconditionally on every delivery. Phase 13 leaves the subscription registration unchanged but **gates the body** behind the buffered contract. Since `after_send` fires only after delivery (which only happens on `result.ok` per the Phase-7 queue), the projection already runs only on success. The Phase 13 change is small: the projection body asserts it is in the buffered region (via a soft ContextVar flag) and logs a warning if not. No functional change to vault behavior.

4. **Why the WriteBuffer is more than ceremony:** `master.handle` currently commits the assistant message immediately after execute returns ok, BEFORE governance writes its decision row. If governance later returns `reject` (Phase 10 path), master rewrites `result` to the rejection text and then commits. That order works, but the buffer makes it impossible to commit-out-of-order accidentally. Phase 14 will add real governance pre-commits that depend on this guarantee.

5. **Memory single-writer rule still holds.** The buffer's `stage(...)` callbacks ultimately call `default_memory_agent.commit_assistant_turn(...)`, which enters the `memory_writer()` ContextVar region. Nothing changes the rule that only MemoryAgent writes to durable memory.

6. Tests in `tests/test_memory_write_buffer.py` (~6 new):
   - `test_commit_writes_staged_callables_in_order`.
   - `test_drop_does_not_write`.
   - `test_double_commit_raises`.
   - `test_context_manager_drops_if_neither_called`.
   - `test_master_workflow_failure_drops_buffer`: synthetic failing workflow → no assistant message written.
   - `test_master_workflow_success_commits_buffer`: happy path → assistant message persists.

**Files added:** `src/ubongo/memory/write_buffer.py`, `tests/test_memory_write_buffer.py`.
**Files modified:** `src/ubongo/master.py` (wrap handle body in `with workflow_buffer() as buf`).

### 13e — `repair_runs` audit table

**Purpose:** Every repair attempt persists a row so `/trace` can show what was tried.

**Tasks:**

1. Schema addition in `src/ubongo/memory/schema.sql`:

   ```sql
   CREATE TABLE IF NOT EXISTS repair_runs (
     id INTEGER PRIMARY KEY,
     workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
     agent_run_id INTEGER REFERENCES agent_runs(id),  -- the failing run; NULL if abort-before-any-retry
     agent TEXT NOT NULL,                             -- the agent that failed
     failure_kind TEXT NOT NULL,                      -- FailureKind value
     original_error TEXT,                             -- the AgentResult.error string
     strategy_attempted TEXT NOT NULL,                -- RecoveryStrategy value
     peer_agent TEXT,                                 -- when strategy=replace_with_peer
     override_model TEXT,                             -- when strategy=*_model_*
     attempt_index INTEGER NOT NULL,                  -- 0-based across the chain
     outcome TEXT NOT NULL CHECK (outcome IN ('recovered', 'failed', 'aborted')),
     started_at TIMESTAMP NOT NULL,
     ended_at TIMESTAMP
   );

   CREATE INDEX IF NOT EXISTS idx_repair_runs_workflow ON repair_runs(workflow_run_id);
   ```

2. Migration shim in `store.bootstrap()` (mirror `_migrate_agent_runs_retried_column` pattern): if the table doesn't exist, the `CREATE TABLE IF NOT EXISTS` covers it. No column-add migration needed (table is new).

3. New writer `store.append_repair_run(...)` with the columns above. Returns the new row id.

4. New reader `store.repair_runs_for(workflow_run_id) -> list[dict]` joined into the existing `last_n_workflow_runs` helper used by `/trace`.

5. `/trace` renderer (`src/ubongo/repl.py::_render_trace`) gains a per-agent "repair:" line:

   ```
   agents:
     critic     openrouter/anthropic/claude-sonnet-4.5  failure    412ms  ... (retried)
       repair: kind=content_rejection  strategy=replace_with_peer  peer=architect  outcome=recovered
     architect  openrouter/anthropic/claude-sonnet-4.5  success   1820ms  ... (peer)
   ```

   When multiple strategies tried, one indented `repair:` line per attempt, in order.

6. `workflow_runs.outcome='repaired'`: set in `master.handle` when `any_repair_fired and result.ok`. Wire via a flag the runner returns alongside `WorkflowResult` (extend the dataclass with `repair_fired: bool = False`; the runner sets it true when `_recover_or_give_up` returns a non-failed-result and at least one repair_runs row was written).

7. Tests in `tests/test_memory_store.py` (+2): `append_repair_run` + `repair_runs_for(workflow_run_id)` happy path. Tests in `tests/test_repl_trace.py` (+2): /trace shows repair lines; /trace omits repair lines when no repair fired.

**Files modified:** `src/ubongo/memory/schema.sql`, `src/ubongo/memory/store.py` (+append/read helpers), `src/ubongo/runner.py` (write repair_runs from `_recover_or_give_up`), `src/ubongo/master.py` (set `outcome='repaired'`), `src/ubongo/repl.py` (trace renderer), `tests/test_memory_store.py`, `tests/test_repl_trace.py`.

### 13f — Unrecoverable apology + y/n flow

**Purpose:** When all strategies are exhausted, master returns a clear apology AND tells the caller it wants a user decision. REPL prints `y/n`; one-shot prints + exits.

**Tasks:**

1. `WorkflowResult` extension (in `master.py`):

   ```python
   @dataclass(frozen=True)
   class WorkflowResult:
       text: str
       ok: bool
       tokens_in: int
       tokens_out: int
       model: str
       latency_ms: int
       evaluator_confidence: float | None = None
       repair_fired: bool = False
       requires_user_decision: bool = False    # 13f: set when all strategies aborted
       repair_summary: dict | None = None      # 13f: {attempts: N, last_kind: ..., last_strategy: ...}
   ```

2. Master sets `requires_user_decision=True` when `result.ok is False AND repair_fired is True` (i.e., the runner tried recoveries and gave up). The runner gives master a `repair_summary` dict aggregating the attempts.

3. Apology text composed in master from the summary:

   ```
   I couldn't recover from a {last_kind} in the {agent_name} step
   after {attempts} repair attempts. Want to try again from a fresh
   prompt? (y/n)
   ```

4. `Response` dataclass gains `requires_user_decision: bool` so REPL / oneshot can branch.

5. REPL changes (`src/ubongo/repl.py`):

   ```python
   if response.requires_user_decision:
       print(response.text)
       choice = input("(y/n) ").strip().lower()
       if choice == "y":
           # Re-issue the previous user message ONCE. If it fails again,
           # fall through to a normal apology print + return to prompt.
           ...
       else:
           # Return to a clean prompt; user can try a different question.
           ...
   ```

   Cap the re-issue at one round; chained y/n loops are a Phase-14 governance concern.

6. One-shot changes (`src/ubongo/oneshot.py`): print `response.text` and exit rc=1 when `requires_user_decision` is True. No prompt (one-shot is non-interactive).

7. Tests in `tests/test_master.py` (+3): unrecoverable returns `requires_user_decision=True`; `repair_summary` populated; non-repair failures stay `requires_user_decision=False`. Tests in `tests/test_repl.py` (+2): /repl renders y/n on the unrecoverable case; y reissues; n returns to prompt.

**Files modified:** `src/ubongo/master.py`, `src/ubongo/repl.py`, `src/ubongo/oneshot.py`, `tests/test_master.py`, `tests/test_repl.py`.

### 13g — STATUS + smoke playbook Phase 13 section

**Tasks:**

1. Append Phase 13 section to `tests/manual/smoke_test.md` with these scenarios:

   | # | Scenario | Steps | Expected |
   | --- | --- | --- | --- |
   | 13.1 | Parse-error recovery via variant prompt | `uv run pytest tests/test_runner.py::test_repair_parse_error_recovers_with_variant_prompt` | Pass. `agent_runs` shows evaluator with `retried=True`; `repair_runs` row has `failure_kind='parse_error'`, `strategy='retry_same_model_variant_prompt'`, `outcome='recovered'`; `workflow_runs.outcome='repaired'`. |
   | 13.2 | Model-error walks the chain | `uv run pytest tests/test_runner.py::test_repair_model_error_walks_to_smaller_model` | Pass. Three `agent_runs` rows for the same agent (orig + 2 retries); three matching `repair_runs` rows; last one `outcome='recovered'`. |
   | 13.3 | Collaborative critic_no_candidate replaced with peer | `rm -f data/ubongo.db`; in REPL: `/mode brief_collaborative`; `give me a brief on adopting microservices` | Merged document has all three role headings present (no `critic` section missing). `sqlite3 data/ubongo.db "SELECT strategy_attempted, peer_agent, outcome FROM repair_runs"` → `replace_with_peer, architect, recovered`. |
   | 13.4 | Memory failure not retried | `uv run pytest tests/test_runner.py::test_repair_unrecoverable_short_circuits` | Pass. Memory failure produces ONE agent_runs row + ONE repair_runs row with `strategy='abort'`. |
   | 13.5 | Unrecoverable apology + y/n (REPL) | inject persistent failure via test fixture (`tests/test_repl.py::test_repl_unrecoverable_prompts_yn`) | Pass. REPL prints `I couldn't recover ... (y/n)`; `n` returns to clean prompt; `y` reissues the prior message once. |
   | 13.6 | Unrecoverable in one-shot exits rc=1 | with mocked persistent failure, `uv run python -m ubongo send "trigger fail" --persona casual` (under test harness) | stdout has the apology; rc=1; no y/n prompt. |
   | 13.7 | `workflow_runs.outcome='repaired'` set after recovery | After 13.1: `sqlite3 data/ubongo.db "SELECT outcome FROM workflow_runs ORDER BY id DESC LIMIT 1"` | `repaired`. |
   | 13.8 | `workflow_runs.outcome='failure'` set after exhausted recovery | After 13.5: `sqlite3 data/ubongo.db "SELECT outcome FROM workflow_runs ORDER BY id DESC LIMIT 1"` | `failure`. |
   | 13.9 | `/trace` renders repair lines | After 13.3: REPL `/trace 1` | Trace output for that workflow_run contains a `repair: kind=content_rejection strategy=replace_with_peer peer=architect outcome=recovered` line under the critic row. |
   | 13.10 | Write-buffer drops on failure | After 13.5: `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM messages WHERE role='assistant' AND conversation_id=(SELECT MAX(id) FROM conversations)"` | 0. No assistant turn was written; the buffer dropped. The vault file for that conversation also did not gain an entry for that turn. |
   | 13.11 | Pytest passes | `uv run pytest tests/` | All green (~395 expected after Phase 13: Phase-12's 382 + ~13 new across repair/runner/store/repl/master). |

2. Patch stale items the 2026-05-15 smoke walkthrough flagged:
   - Scenario 2.5: update expected count language from "twice — single retry" to "6× `llm_attempt_failed` (2 per call × 3 calls: classifier + persona + Phase-11/13 repair retry)" and rename `llm_error` → `persona_llm_error`. Note that Phase 13 may push the count higher when multi-strategy retry kicks in.
   - Scenario 11.9: rewrite the query to use a row-count delta (record `workflow_runs` count before the `/exec` block; expect 0 new rows after) instead of `LIKE '%exec%'` which now matches every row because Phase-12 added `"execution_mode"` to the workflow JSON.

3. Update `STATUS.md`: Phase 13 row → Complete (date); overall paragraph rewritten; LOC count bumped.

**Files modified:** `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 13

```text
src/ubongo/
  agents/
    repair.py                             (rewrite — FailureKind, RecoveryStrategy, RecoveryPlan, plan_recovery; plan_retry retired)
    personas.py                           (one-line — read repair_prompt_hint from metadata)
    coding.py                             (one-line — same)
    research.py                           (one-line — same)
    evaluator.py                          (one-line — same)
    critic.py                             (one-line — same)
  runner.py                               (replace single-retry block with _recover_or_give_up + _maybe_replace_failed; fan-out modes wire the latter)
  master.py                               (wrap handle in workflow_buffer; set outcome='repaired'; emit repair_summary + requires_user_decision)
  repl.py                                 (y/n flow for unrecoverable; /trace renders repair lines)
  oneshot.py                              (rc=1 + apology when requires_user_decision)
  memory/
    schema.sql                            (+ repair_runs table)
    store.py                              (+ append_repair_run + repair_runs_for + bump last_n_workflow_runs join)
    write_buffer.py                       (new — WriteBuffer + workflow_buffer context manager)
config/
  settings.yaml                           (+ agents.repair.max_attempts; + agents.repair.peer_replacements)
tests/
  test_agents_repair.py                   (rewrite — ~12 cases for plan_recovery + FailureKind classification)
  test_runner.py                          (+ ~9 cases — parse_error variant, model_error walk, exhausted, max_attempts cap, repair_runs persisted, unrecoverable short-circuit, collab peer replace, parallel no-replace, speculative no-replace)
  test_memory_write_buffer.py             (new — ~6 cases)
  test_memory_store.py                    (+ 2 — append_repair_run + repair_runs_for)
  test_master.py                          (+ 3 — requires_user_decision + repair_summary + non-repair failures)
  test_repl.py                            (+ 2 — y/n flow)
  test_repl_trace.py                      (+ 2 — repair line rendered / suppressed)
Plans/
  phase-13-repair.md                      (new — this file)
STATUS.md                                 (modified — Phase 13 row + overall paragraph + LOC)
tests/manual/smoke_test.md                (modified — Phase 13 section appended; 2.5 + 11.9 patches)
```

Untouched: `classifier.py`, `context.py`, `events.py`, `governance/*` (Phase 14 territory), `delivery/queue.py`, `memory/compaction.py`, `memory/vault.py`, `agents/memory.py` (the projection body adds an assertion but no functional change), `agents/execution.py`, `sandbox.py`, `skills.py`, `config/skills/*`, `config/UBONGO.md`, `config/personas/*`, `config/routing.yaml`, `config/workflows.yaml` (no workflow definitions change; Phase 13 is recovery, not new workflows).

## Open questions to confirm before I start

1. **Failure taxonomy (approved 2026-05-15).** `timeout | model_error | parse_error | content_rejection | precondition_missing | infinite_loop | unrecoverable`. `precondition_missing` (Option A) is the input-contract-not-met bucket; its ladder skips variant-prompt retries and leads with peer replacement so `critic_no_candidate` doesn't waste an attempt re-prompting the same critic with the same empty input. `infinite_loop` is declared but the detector is a no-op v0.1 (no agent-to-agent invocation cycle exists yet).
2. **Strategy ordering per kind (recommended).** The matrix in 13b §2: PARSE_ERROR → variant-prompt → peer → abort; MODEL_ERROR → different-model → smaller-model → peer → abort; TIMEOUT → smaller-model → peer → abort; CONTENT_REJECTION → variant-prompt → peer → abort. The recommended order is "cheapest fix first, peer replacement before abort". OK?
3. **`max_attempts: 3` cap (recommended).** Caps total strategy attempts per failure regardless of list length. 3 is enough to walk the longest list (MODEL_ERROR: different → smaller → peer). Configurable in `settings.yaml::agents.repair.max_attempts`. OK?
4. **Peer replacements (recommended defaults).** `critic: architect` (fixes 12.4), `coding: architect`, `research: architect`. `evaluator: null` (LLM-as-judge is structurally unique). `memory: null`, `execution: null` (by design). Personas have no peer (`architect/operator/casual: null`) — replacing a persona is a routing concern, not a repair concern. OK?
5. **Fan-out modes get peer-replacement only, not multi-strategy retry (recommended).** Multi-strategy in `asyncio.gather` would mean cancel-others-on-first-failure-and-retry, which v0.1 can't do cleanly. Peer replacement is a single-hop substitute and gets us the 12.4 fix. Phase 14 may revisit. OK?
6. **`WriteBuffer` covers only the assistant-message commit + the after_send vault projection in v0.1 (recommended).** No agent today stages mid-flight writes outside Memory's post-success commit. Phase 19/20 agents (when they exist) get the same interface. OK?
7. **Unrecoverable: REPL prompts `y/n`; one-shot prints apology + rc=1 (recommended).** Re-issue cap = 1 round. Chained y/n loops are a Phase-14 governance concern. OK?
8. **`workflow_runs.outcome='repaired'` set on any successful workflow where a repair_runs row landed and the final result was ok (recommended).** Otherwise stays `success` (no repair) or `failure` (unrecoverable). OK?
9. **`/trace` renders repair lines inline under the affected agent_run (recommended).** No standalone `/repair` command in Phase 13. OK?
10. **Patch the 2.5 + 11.9 smoke-playbook drift items in 13g (recommended).** Not a Phase-13 deliverable per se, but they were the only two non-functional drift findings from the 2026-05-15 walkthrough — patching them in 13g keeps the playbook honest for the Phase 13 smoke section. OK?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 13

- 8 commits on `phase-13-repair` (Plan + 13a–13g). Push the branch and open the draft PR immediately after the Plan commit.
- Smoke scenarios 13.1–13.11 pass; 13.11 pytest green (~395 expected).
- New tests: rewrite `test_agents_repair.py` (~12), `test_runner.py` +9, `test_memory_write_buffer.py` (~6 new file), `test_memory_store.py` +2, `test_master.py` +3, `test_repl.py` +2, `test_repl_trace.py` +2. Existing tests still pass with the listed updates.
- 12.4's `critic_no_candidate` is now a recovered turn end-to-end (smoke 13.3); collaborative mode produces all three role sections on a working brief_collaborative run.
- `tests/manual/smoke_test.md` Phase 13 section appended; 2.5 and 11.9 patched.
- `STATUS.md` Phase 13 row → Complete; "Overall" paragraph refreshed; LOC count bumped.
- Branch handed to you for merge. **Don't merge.**

---

(Verified: `origin/main` matches local `main` at `4bf6c0f`. Phase 12 fully merged; 2026-05-15 smoke walkthrough green except the 12.4 evaluator parse + critic_no_candidate items Phase 13 explicitly addresses. Branch `phase-13-repair` does not yet exist.)
