# Ubongo

The domain language of Ubongo: a single-user, multi-agent AI mind that runs locally as a CLI, with a self-improving (genetic-programming) runtime. This glossary names the concepts the code is built around, so that issues, refactors, and tests use one vocabulary.

## Orchestration

**Worker Agent**:
A disposable unit the Master Agent dispatches to do one job in a turn (Research, Coding, Critic, Evaluator, Execution, Persona, Memory, Repair). It satisfies the `Agent` interface: `name`, `role`, `default_model`, and `run(input, context) -> AgentResult`.
_Avoid_: service, component, worker (bare).

**Model call**:
How a Worker Agent reaches the model. Every LLM-calling agent's `run()` builds its own system prompt and messages, then hands them to the shared **model-call envelope** `ubongo.agents.llm_run.run_agent_llm` (or `call_model_or_none` for the `… | None` callers, `evaluator.rank` / `agree`). The envelope owns the parts mechanical to every call: the monotonic timer, `override_model` / `max_tokens_override` resolution off `input.metadata`, the `LLMError → AgentResult(ok=False, error="<name>_llm_error")` mapping, the `"<name>_run"` log line, and the success-result assembly. It in turn calls `ubongo.llm.complete(system_prompt, messages, model, max_tokens, temperature=None)`, which still owns the single retry, token/latency accounting, and `before_llm`/`after_llm` events. What stays in each agent's own `run()` is the part that actually differs: prompt assembly, the repair-hint append (`input.metadata['repair_prompt_hint']`), and result interpretation — the Evaluator passes an `on_success` hook to parse its JSON behind the envelope. The envelope is a mechanical seam, not an invocation/routing layer: it makes no decision about which model, persona, or workflow runs. Agents pass their own module-level `complete` in as `complete_fn` so the call stays patchable per agent.
_Avoid_: invocation, request, LLM step.

**Composer**:
The one Worker Agent in a workflow whose output becomes the user-facing response. Marked by a `composer = True` attribute; `WorkflowResult.text` is taken from the last composer to run. Validators (Evaluator, Critic) and helpers (Research, Execution) contribute findings but are not composers.
_Avoid_: responder, finalizer.

**Finding**:
What a non-composer Worker Agent returns for downstream agents to build on, threaded forward as `prior_findings`. A Finding is evidence or critique, never the durable record and never (by itself) the response.
_Avoid_: result (bare), output.

**Execution mode**:
The strategy the WorkflowRunner uses to run a workflow's agents: `sequential`, `parallel`, `competitive`, `collaborative`, `debate`, or `speculative`. Selected off `workflow.execution_mode`; the runner is async internally but sync at its `execute()` boundary.
_Avoid_: strategy (bare), pipeline.

**Governance decision**:
The gate the Master Agent applies before composing: a matrix over `risk` / `confidence` / `reversibility` returning `auto` | `ask_clarification` | `require_approval` | `reject` (config in `governance.yaml`). `require_approval` becomes an interactive `y/n/why` prompt.
_Avoid_: policy check, guardrail (bare).

## CLI

**Slash command / Command registry**:
A **Slash command** is a REPL control or diagnostic input (`/trace`, `/mode`, `/evolution`, `/improvements`, …) — distinct from a **turn**, which is ordinary user text routed through the Master pipeline. Slash commands are dispatched over the **Command registry** seam (`ubongo.commands`: a `name → Command(handler, usage)` map; the loop looks up and runs the handler, instead of an inline branch per command). Handlers are pure — they take the command line plus the mutable `ReplState` and return text; the loop owns I/O. The help banner is derived from the registry. Persona switches (`/architect|operator|casual`), `/auto`, and `/exit` stay in `handle_slash` (their tuple contract is tested directly); the loop falls back to it for unregistered heads. Command output is delivered through `notification_queue` like any outbound message (ADR-0002), tagged `source="command"`.
_Avoid_: command handler (bare), dispatcher (bare), REPL command (use "slash command").

## Self-improvement (genetic programming)

**Evolvable Target** (and its **kind**):
Something the GP layer can mutate, addressed by a string. Two kinds: **prompt** targets — the persona prompts `persona:architect|operator|casual`; and **config** targets — `routing:default`, `toolchain:<workflow>`, `retry:repair`. A target's _base_ is its current live text/config, or the promoted active variant when one exists.
_Avoid_: knob, parameter, gene.

**Variant**:
A single mutated candidate of a target, persisted to `evolution_lineage` (`variant_text` holds an alternate persona body or a serialized config). Prompt variants come from LLM mutation strategies (paraphrase / prune / expand / recombine / perturb-temperature); config variants from deterministic, validated structural mutations.
_Avoid_: candidate (bare), version, mutation (as a noun for the row).

**Generation / Lineage**:
Variants are produced in numbered **generations** per target. **Lineage** is the cross-generation chain: a generation is seeded from the previous one's champion survivor, recorded via `parent_id`. The `evolution_lineage` table is the lineage record.
_Avoid_: batch, round, epoch.

**Fitness**:
A variant's score: a cohort-normalized weighted sum over five signals (success rate, cost, latency, hallucination rate, user-correction rate; weights in `evolution.fitness_weights`). Prompt and routing/tool-chain variants are judged by running them and scoring the responses; retry variants use a documented **structural proxy** (offline samples can't induce failures).
_Avoid_: score (bare), quality.

**Survivor**:
The top-K variants of an evaluated generation by fitness. The champion (rank 1) seeds the next generation's mutations.
_Avoid_: winner (reserve for competitive-mode ranking), best (bare).

**GP Loop**:
The autonomous background daemon (`EvolutionLoop`) that runs one **cycle** at a time: pick the stalest target, generate a generation (seeded from survivors), evaluate it, propose a promotion if warranted. Throttled by a rolling-hour call budget, paced by `evolution.cron`, and pausable via `/evolution`. Starts paused.
_Avoid_: trainer, optimizer (bare), scheduler (bare).

**Promotion / Active Evolution / Live Swap**:
The loop **proposes** a promotion (`pending_promotions`) when a champion beats the active baseline by `evolution.promotion_margin`. The user **approves** via `/improvements`. Approval writes an **active evolution** (`active_evolutions`, one per target) and performs a **live swap**: the runtime read paths (`context.build_system_prompt` for personas, `router.route_workflow` / `router.workflow_agents` for config) consult `active_evolutions`, so behavior actually changes. Promotion is approved, never autonomous.
_Avoid_: deploy, rollout, activation (bare).

## Memory

**Durable memory / single writer**:
The canonical record (SQLite via `memory/store.py`, the projected Markdown vault, and embeddings). The **Memory Agent** is the only Worker Agent that writes it; other agents return Findings, the Memory Agent commits. Every outbound message also passes through `notification_queue`.
_Avoid_: database (bare), persistence layer.

**Recency window vs. Semantic recall**:
The two ways recall surfaces context for a turn. **Recency** is the last-N messages of the conversation. **Semantic recall** embeds the current query (`sqlite-vec`) and retrieves the most similar prior messages that fall _outside_ the recency window, returned on `RecallContext.semantic_messages`. Both are best-effort: with embeddings disabled or unavailable, recall degrades cleanly to recency-only.
_Avoid_: history (bare), context window (ambiguous).

**Vault-link graph**:
The graph formed by `[[wikilinks]]` in daily notes, recorded in `vault_links` and traversed via `memory/graph.py` (`neighbors`, `backlinks`, bounded `traverse`). Distinct from the lineage graph (that is evolution; this is notes).
_Avoid_: knowledge graph (overclaims), link index.

**Vault sync / Ingest**:
Bidirectional vault flow. The system _projects_ turns into daily notes (outbound); the **`VaultWatcher`** poller _ingests_ external edits you make in Obsidian (inbound) — re-embedding them into `vec_vault`. It tells its own writes from your edits via `vault_state` (the hash the system last wrote): match → its write, skip; differ → your edit, ingest. "Ingest" is always the inbound direction.
_Avoid_: sync (bare, directionless), watch (bare).

**Conflict**:
An external edit to a vault note the system also manages, queued in `vault_conflicts` for the user to resolve via `/conflicts` (keep-mine / keep-theirs / merge). For append-only daily notes the practical resolution is "coexist."
_Avoid_: collision (use only in prose), merge conflict (git connotation).

**Audit entry**:
One categorized row in the unified `vault/system/audit.md` — `category ∈ governance | evolution | sync`. A human-readable record of every gated decision, promotion, and ingest; the file is the source of truth, `/audit` tails it.
_Avoid_: log (bare), event (reserved for the event bus).

## Example dialogue

> **Dev:** When the GP loop says a persona variant "beat baseline," what actually changes after I approve it?
> **Domain expert:** Approving writes an `active_evolutions` row for that target. The live swap means `build_system_prompt` now reads the promoted `variant_text` as the persona body instead of the file. The agent's model call is unchanged — same `complete()` seam — it just gets a different system prompt. Roll it back and the read path reverts to the file.
> **Dev:** And a routing variant?
> **Domain expert:** Same promotion machinery, different read path: `router.route_workflow` consults the promoted routing config. Its fitness came from running the real pipeline on held-out samples under an isolated, side-effect-free override and judging the responses — not from a structural guess. Retry config is the one target scored by a structural proxy, because offline samples can't trigger failures.
