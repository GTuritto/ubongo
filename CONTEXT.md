# Ubongo

The domain language of Ubongo: a single-user, multi-agent AI mind that runs locally as a CLI, with a self-improving (genetic-programming) runtime. This glossary names the concepts the code is built around, so that issues, refactors, and tests use one vocabulary.

## Orchestration

**Worker Agent**:
A disposable unit the Master Agent dispatches to do one job in a turn (Research, Coding, Critic, Evaluator, Execution, Persona, Memory, Repair). It satisfies the `Agent` interface: `name`, `role`, `default_model`, and `run(input, context) -> AgentResult`.
_Avoid_: service, component, worker (bare).

**Connector agent** (`agents/connector.py`, candidate 20 / v0.1.5):
The ninth Worker Agent (`composer=False`) and Ubongo's only door to external services: it discovers the tools offered by the MCP servers declared in `settings.yaml::mcp.servers`, plans zero or more calls with its model (tolerant JSON plan), executes them through `mcp/client.py` (per-turn sessions, stdio or streamable HTTP), and returns the results as a Finding. Reached only via `/mode connector_session` (not auto-routed); any workflow containing it scores **irreversible**, and turn risk escalates to the highest enabled server's declared `risk:` (ADR-0016). Honest degradation: no SDK / no servers / no applicable tool are `ok=True` findings; failed calls are `connector_mcp_error`, repairable by peer replacement (architect).
_Avoid_: tool agent (bare), integration agent, MCP agent (say Connector agent).

**Model call**:
How a Worker Agent reaches the model. Every LLM-calling agent's `run()` builds its own system prompt and messages, then hands them to the shared **model-call envelope** `ubongo.agents.llm_run.run_agent_llm` (or `call_model_or_none` for the `… | None` callers, `evaluator.rank` / `agree`). The envelope owns the parts mechanical to every call: the monotonic timer, `override_model` / `max_tokens_override` resolution off `input.metadata`, the `LLMError → AgentResult(ok=False, error="<name>_llm_error")` mapping, the `"<name>_run"` log line, and the success-result assembly. It in turn calls `ubongo.llm.complete(system_prompt, messages, model, max_tokens, temperature=None)`, which still owns the single retry, token/latency accounting, and `before_llm`/`after_llm` events. What stays in each agent's own `run()` is the part that actually differs: prompt assembly, the repair-hint append (`input.metadata['repair_prompt_hint']`), and result interpretation — the Evaluator passes an `on_success` hook to parse its JSON behind the envelope. The envelope is a mechanical seam, not an invocation/routing layer: it makes no decision about which model, persona, or workflow runs. Agents pass their own module-level `complete` in as `complete_fn` so the call stays patchable per agent.
_Avoid_: invocation, request, LLM step.

**Agent directives**:
The typed control signals the orchestrator passes down to an agent for one run, carried on `AgentInput.directives` as a frozen `AgentDirectives` (`override_model`, `max_tokens_override`, `repair_prompt_hint`, `debate_role`, `skill`, `exec_command`). It replaced the old untyped `metadata` string keys, so a misspelled directive fails at construction instead of silently no-op'ing. Distinct from `AgentInput.metadata`, which stays an open dict for the **Memory agent's commit payload** (`conversation_id`, `response_text`, …) — a variable record, not a fixed control surface.
_Avoid_: metadata (for directives), options, params (bare).

**Composer**:
The one Worker Agent in a workflow whose output becomes the user-facing response. Marked by a `composer = True` attribute; `WorkflowResult.text` is taken from the last composer to run. Validators (Evaluator, Critic) and helpers (Research, Execution) contribute findings but are not composers.
_Avoid_: responder, finalizer.

**Finding**:
What a non-composer Worker Agent returns for downstream agents to build on, threaded forward as `prior_findings`. A Finding is evidence or critique, never the durable record and never (by itself) the response.
_Avoid_: result (bare), output.

**Execution mode**:
The strategy the WorkflowRunner uses to run a workflow's agents: `sequential`, `parallel`, `competitive`, `collaborative`, `debate`, or `speculative`. Selected off `workflow.execution_mode`; the runner is async internally but sync at its `execute()` boundary.
_Avoid_: strategy (bare), pipeline.

**Workflow plan**:
What `router.plan_workflow(classification, …)` returns: a validated `WorkflowPlan` (workflow name, chosen persona, agent tuple with the evaluator appended, mode, rounds, timeout) assembled from routing + hysteresis + the `/mode` override + structural mode/agents validation. The router owns config assembly; `master.plan` maps the `WorkflowPlan` to a `Workflow` by adding the persona model and resolved skill — the split is **router = config, master = turn state + registries**. The runner keeps its mode-invariant raises as a registry-aware backstop.
_Avoid_: route result, plan (bare), workflow spec.

**Governance decision**:
The gate the Master Agent applies before composing: a matrix over `risk` / `confidence` / `reversibility` returning `auto` | `ask_clarification` | `require_approval` | `reject` (config in `governance.yaml`). `require_approval` becomes an interactive `y/n/why` prompt.
_Avoid_: policy check, guardrail (bare).

## CLI

**Slash command / Command registry**:
A **Slash command** is a REPL control or diagnostic input (`/trace`, `/mode`, `/evolution`, `/improvements`, …) — distinct from a **turn**, which is ordinary user text routed through the Master pipeline. Slash commands are dispatched over the **Command registry** seam (`ubongo.commands`: a `name → Command(handler, usage)` map; the loop looks up and runs the handler, instead of an inline branch per command). Handlers are pure — they take the command line plus the mutable `ReplState` and return text; the loop owns I/O. The help banner is derived from the registry. Persona switches (`/architect|operator|casual`), `/auto`, and `/exit` stay in `handle_slash` (their tuple contract is tested directly); the loop falls back to it for unregistered heads. Command output is delivered through `notification_queue` like any outbound message (ADR-0002), tagged `source="command"`.
_Avoid_: command handler (bare), dispatcher (bare), REPL command (use "slash command").

**Profiler** (`ubongo.profiling`, candidates 10–12):
The local profiler, three stdlib-only parts behind `/profile`, all opt-in with zero overhead when off and all best-effort (a profiling failure can never break a turn). The **stats part** aggregates the `workflow_runs` / `agent_runs` rows the runner already persists — on demand, read-only (the Memory Agent's single-writer rule untouched) — into a summary plus per-agent / per-model / per-mode breakdowns (avg and nearest-rank p95 latency, tokens, failure and retry rates), optionally windowed to the last N workflow runs. The **CPU part** is armed explicitly (`/profile cpu on`, or `ubongo send --profile`) and wraps the turn's `master.handle` in `cProfile`, dumping `data/profiles/turn-<ts>.prof` and emitting a top-25 cumulative summary. The **memory part** (`/profile mem on`) takes a `tracemalloc` baseline; `/profile mem` diffs current allocations against it (top growth sites, traced now/peak, process RSS) for leak hunting across a long-lived session — the armed state is process-global and lives in the module, not on `ReplState`. The **startup switch** (`--profile [cpu|mem|all|off]` on launch, or `UBONGO_PROFILE` in `.env`; flag wins) arms the same toggles from boot in the REPL and one-shot, and arms CPU on the web turn path (mem stays REPL-only — the web UI has no report surface).
_Avoid_: metrics (this is a diagnostic view, not a telemetry pipeline), tracing (that's `/trace`, the per-run view the profiler aggregates).

**MCP channel** (`ubongo.mcp`, candidate 13 / v0.1.4):
Ubongo as an MCP server — the fourth additive channel (REPL, one-shot, web, MCP), machine-facing where the others are human-facing. `service.py` is the channel-free core: `ubongo_send` calls the one orchestration seam (`master.handle`) exactly like one-shot, so an MCP-driven turn is governed and persisted like a typed one; a `require_approval` turn returns `gated=true` and is **never approvable over MCP** (approval needs a human channel). `ubongo_recall` and the `ubongo://` resources are read-only. `server.py` is the only module importing the optional `mcp` SDK; transports are stdio and streamable HTTP (LAN no-auth posture, ADR-0015). The MCP *client* direction (Ubongo consuming external servers) is a future layer, not this term.
_Avoid_: API (this is a channel, not a REST surface), tool server (bare — say MCP server), integration (vague).

**Channel core** (`ubongo.channel`, candidate 14):
The one turn envelope every channel shares: `bootstrap()` (config + logging once + the `UBONGO_PROFILE` knob; never starts daemons) and `run_turn(message, persona, ...) -> (Response, cpu_report)` (the optional cProfile wrap, `master.handle` resolved at call time, the `notification_queue` flush). The no-bypass rule (ADR-0002/0003) is this function, not a convention: REPL, one-shot, web, and MCP keep only presentation — printing/exit codes, rendering, dict shaping, prompts. A new channel (v0.2 Telegram) starts as a thin adapter over this seam.
_Avoid_: transport layer (bare), pipeline (that's the Master's turn pipeline — the core wraps it, once).

**Daemon loop** (`ubongo.daemon`, candidate 15):
The one lifecycle behind the three background daemons (GP loop, authoring loop, vault watcher): `DaemonLoop` owns the thread, the stop event, the per-cycle exception swallow, the whole-thread crash guard, and both run styles (async for the budgeted loops, sync for the watcher — chosen by the injected sleep); `daemon.should_cycle` is the shared scheduling gate (status / rolling-hour budget / cron) that was once duplicated byte-for-byte. Each daemon subclasses it and keeps only its cycle work, enablement (config + `UBONGO_DISABLE_*` switch — evolution gained its switch here), status seeding, and interval. Started/stopped by the REPL, boots paused where a status row exists.
_Avoid_: scheduler (bare), background job (bare), worker (that's a Worker Agent).

**Standing job** (`ubongo.jobs`, v0.5 phase 06):
A scheduled, _proactive_ turn — the first time Ubongo speaks unprompted. The **`StandingJobsLoop`** is a fourth [[daemon-loop]] (booting paused), which runs config-defined jobs (`config/jobs.yaml`: name, schedule, **grant bundle**, persona, prompt) on their schedule through `master.handle` (no bypass) and delivers via `notification_queue`. A job's **grant bundle** is the capability classes it may use without asking, approved once through the approval seam (ADR-0018) as Phase-05 grants; a run reaching outside the bundle gates, and the job **parks and raises** itself for approve-later. The **proactive policy** (`jobs/policy.py`) is two controls enforced by the queue's deliverability filter: **quiet hours** hold a send behind a future `deliver_after`; a parked raise's TTL `expires_at` **auto-declines** it (default-deny). User-facing output is a distinct queue `source` (`proactive` / `proactive-raise`) **drained** by whatever channel is listening — the REPL as a launch catch-up, the Telegram bot each poll. Runtime state lives in `standing_jobs` / `job_runs` / `jobs_state` (`memory/jobs_state.py`). Controlled by `/jobs status|pause|resume|off|run`.
_Avoid_: cron job (bare), notification (that's the queue row), reminder.

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

## Self-extension (authored skills, post-v0.1)

Where self-improvement _tunes_ existing prompts/config, **self-extension** _authors brand-new skills_ — the `src/ubongo/authoring/` package, behind the same human-approval boundary ([ADR-0013](docs/adr/0013-self-authored-skills-quarantine-and-approval.md)).

**Candidate / draft**: a `SkillCandidate` is drafted by an LLM from a capability description (`/author`) or inferred gap (the daemon): SKILL.md frontmatter + body + optional prompt templates + an optional constrained-bash **command template**. `validation.validate` reuses the `skills._parse_skill` schema and enforces a **command-skill risk floor** (any command-bearing candidate is forced to `risk >= medium` / `irreversible`, in code, not author-declared); a command template is statically vetted by `sandbox.validate_command`.
_Avoid_: skill (a drafted candidate is NOT a live skill until approved).

**Quarantine**: a draft is written to `config/skills_candidates/<name>/` (NOT scanned by `skills.py`) and recorded in `authored_skills`. Invisible to the classifier and `/skills` until approved.
_Avoid_: registered, installed, live (for a quarantined draft).

**Evaluate / quality**: `sandbox.evaluate_candidate` scores a candidate side-effect-free (prompt judge over a few probes + a command dry-run); `fitness.score_candidate` reduces it to a `[0,1]` scalar shown in `/skill-candidates`. An estimate to inform the reviewer, not an autonomous pass/fail.

**Approval gate**: `/skill-candidates approve|reject|rollback` (`authoring/promotion.py`). **Approve** materializes the candidate into the live `config/skills/` (re-validating + backing up any prior version to `config/skills_backups/`) and reloads the registry; **rollback** restores the prior version or unregisters. The human gate is the only path from quarantine to live.

**Authoring daemon**: `AuthoringLoop` (`authoring/loop.py`) mirrors the GP loop — boots paused, throttled by a rolling-hour budget, infers recurring capability gaps (`gaps.next_gap`, intents that matched no skill) and drafts into quarantine. It **only ever drafts**; approval stays manual. Controlled by `/authoring status|pause|resume|off`.
_Avoid_: auto-approve, auto-install (the daemon never does either).

## Memory

**Durable memory / single writer**:
The canonical record (SQLite via `memory/store.py`, the projected Markdown vault, and embeddings). The **Memory Agent** is the only Worker Agent that writes it; other agents return Findings, the Memory Agent commits. Every outbound message also passes through `notification_queue`. One database, five table-family modules (v0.5 phase 02): `store.py` keeps connection/bootstrap plus the per-turn core (conversations, messages, summaries, sessions, recall); `trace.py` owns the four trace tables and their view builders; `evolution_state.py`, `authoring_state.py`, and `index_state.py` own their subsystems' rows. The single-writer rule is about writers, not files — it is unchanged.
_Avoid_: database (bare), persistence layer, "the store" for a seam module (name the module).

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
