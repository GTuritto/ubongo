# Ubongo — State of the Build

A ground-truth read of what is actually in the tree as of 2026-06-12, checked against
[UBONGO_BUILD.md](UBONGO_BUILD.md) (the v0.1 spec). [STATUS.md](STATUS.md) is the
phase-by-phase changelog; this file is the complement: where the code stands, where it
drifted from the spec, the decisions that produced the drift, and what is parked.

If STATE.md and the spec disagree, the code is the source of truth and STATE.md records why.

## Snapshot

- **Current version: v0.1.5.** v0.1 (the 22-phase build) plus the optional web UI (v0.1.1),
  self-authored skills (v0.1.2), the local profiler + service control (v0.1.3,
  [ADR-0014](docs/adr/0014-local-only-observability-profiler.md)), the MCP server channel
  (v0.1.4, [ADR-0015](docs/adr/0015-mcp-server-additive-channel.md)), and the MCP client /
  Connector agent (v0.1.5, [ADR-0016](docs/adr/0016-connector-agent-external-tools-one-seam.md)).
  Plus the second deepening pass (candidates 14/15/17/18; 16 dropped, 19 trigger-parked).
  Not yet v0.2 (Telegram).
- **v0.1 is complete and merged to `main`.** All 22 phases (0–21) landed, each on its own
  `phase-N-<name>` branch, merged after review (16 phase PRs).
- **Plus a post-v0.1 layer that is also on `main`**: six architecture-deepening refactors
  (PRs #20, #22, #24, #25, #26, #27, #28), an optional web channel (PRs #29, #30, #31), and
  the **self-authored-skills** experiment (the `authoring/` package, ADR-0013), and the
  **local profiler + service control** layer (PRs #32, #33, #34: `profiling.py`, the `/profile`
  family, the `--profile`/`UBONGO_PROFILE` startup switch, `ubongo-ctl.sh`, the systemd unit —
  ADR-0014), and the **MCP server channel** (PR #37: the `mcp/` package, `ubongo mcp` stdio/HTTP,
  ctl + systemd + installer support — ADR-0015). These are *not* in the v0.1 spec; see Drift below.
- **Size:** ~15,400 LOC under `src/` (11,255 at v0.1 certification; deepening + web ~800;
  authoring ~1,285; profiler ~650; MCP server ~235; MCP client + Connector ~430; the second
  deepening pass was net-negative). **First crossing of the ~15,000 soft target** (~+1.3%):
  acceptable for the two MCP halves, but the budget is spent — v0.2 should add a transport,
  not another subsystem, and candidate 19's trigger (a growing store.py) doubles as the
  shrink-review trigger.
- **Tests:** 960 pytest, green (874 + 41 profiler + 14 MCP server + 11 deepening + 20 MCP client). The spec's `tests/` layout listed ~16 files; the actual suite
  is far broader (one test module per real module, plus REPL, live-swap, recovery, evaluation,
  sync, audit, and the six authoring suites).
- **Stack matches spec:** Python 3.11+, LiteLLM over OpenRouter, stdlib SQLite, `sqlite-vec`,
  YAML config, `.env` secrets, uv. No LangGraph / Temporal / Ray / Redis / Docker. The only
  dependency added beyond the spec table is `streamlit`, and it is an optional extra (see Drift).

## What is actually built (spec coverage)

Every v0.1 acceptance criterion (the 24 in the spec / 26 enumerated in STATUS) is met. By tier:

- **Foundation (0–7).** Config loader with env-ref resolution; hierarchical `build_system_prompt`;
  JSON logging; REPL + one-shot; LiteLLM wrapper; tone classifier + `routing.yaml` auto-routing
  with hysteresis; SQLite store + session model + swappable compaction; Markdown vault projection;
  skills with progressive disclosure (`summarize-conversation`); SQLite-backed outbound queue that
  *every* response flows through.
- **Multi-Agent (8–12).** Master Agent (classify → plan → execute → govern → compose → enqueue →
  memory). Ten registered agents covering all eight spec roles (Research, Coding, Evaluator, Repair,
  Memory, Critic, Execution, and three Persona classes Architect/Operator/Casual). All six execution
  modes in the runner (sequential, parallel, competitive, collaborative, debate, speculative),
  selectable via `/mode`.
- **Self-Healing (13).** Repair Agent with a seven-category failure taxonomy and an ordered recovery
  ladder (variant prompt → different model → smaller model → peer replacement → abort), `max_attempts=3`,
  `repair_runs` audit table, write-buffer rollback (commit-on-success / drop-on-failure).
- **Governance (14–15).** Five-rule decision matrix over risk/confidence/reversibility from
  `governance.yaml`; interactive y/n/why approval gate; hardened Execution sandbox (allowlist, no
  shell metacharacters, no path traversal, empty child PATH, repo-root cwd, 10s timeout); `docs/SECURITY.md`.
- **Self-Improvement (16–19).** GP loop over prompts *and* config targets (`routing:default`,
  `toolchain:<workflow>`, `retry:repair`): variant generation (`/optimize`), sandboxed fitness
  evaluation against 33 held-out conversations (`/evaluate`), autonomous throttled background loop
  (`/evolution status|pause|resume|off`, comes up paused), human-approved promotion with live swap
  (`/improvements approve|reject|rollback`).
- **Wiki Memory + Polish (20–21).** Semantic recall via `sqlite-vec` folded into turn context;
  vault-link graph from `[[wikilinks]]`; `/recall`; bidirectional vault sync via a polling watcher
  (off by default) with a conflict queue; unified `vault/system/audit.md` and `/audit`; config +
  router hot-reload on `/reload`.
- **Self-authored skills (post-v0.1, `authoring/`).** Beyond the v0.1 spec: Ubongo drafts
  brand-new skills, manually (`/author`) and autonomously (the `AuthoringLoop` daemon — boots
  paused, throttled, infers recurring capability gaps), scores them side-effect-free, and you
  approve them into live capabilities via `/skill-candidates approve|reject|rollback` with
  versioned backups. Drafts are quarantined (invisible to the runtime) until approved; a
  command-skill risk floor and static sandbox validation are enforced in code (ADR-0013). The
  daemon only ever drafts — approval stays manual.

## Drift from the spec

The build is faithful to the spec's intent. The deviations below are real and mostly conscious;
each is either documented in an ADR or in a STATUS phase note.

### Structural — files that differ from the spec's File Structure

| Spec said | Reality | Why |
| --- | --- | --- |
| `src/ubongo/composer.py` | Does not exist | Composition was folded into the runner (the `composer = True` agent attribute selects the user-facing text) and the Persona Agent classes. No separate module earned its keep. |
| `evolution/loop.py` triggered by `/evolve <target>` | Manual entry is `/optimize` + `/evaluate`; autonomous is `/evolution`; loop logic split across `loop.py` (autonomous) and `evolution/manual.py` (user-driven) | The single `/evolve` verb in the spec split into generate / evaluate / run-loop as the surface clarified across Phases 16–18. |
| (not in spec) | `src/ubongo/invoke.py`, `commands.py`, `agents/llm_run.py` | **Post-v0.1 deepening.** Shared agent-invocation core (candidate 02), slash-command registry (candidate 04), one model-call envelope behind every LLM agent (candidate 05). See ADR-0012. |
| (not in spec) | `memory/trace.py`, `memory/write_buffer.py`, `memory/vault_watch.py` | Trace read deepened into a view (candidate 03); write-buffer rollback (Phase 13d); vault-sync polling daemon (Phase 21). |
| (not in spec) | `src/ubongo/web/` | **Out-of-spec web channel.** See below. |
| `governance.yaml` with `risk_rules` / `decision_thresholds` keys | Restructured around `thresholds.critic_band`, `thresholds.auto_route_min_confidence`, the require-approval rules, and the destructive-keyword backstop | The matrix design firmed up in Phase 14; `governance.yaml` became the single config home (the `governance:` block left `settings.yaml`). |
| `settings.yaml evolution.generations_per_run` | `evolution.survivors` + `evolution.cron` + `evolution.promotion_margin` + `evolution.samples_per_eval` added | The loop's real knobs emerged in Phases 17–19. |

### Behavioral / scope deviations

- **Web UI (the largest scope deviation).** The spec lists "Web UI / mobile apps" under *Out of
  Scope (v0.1)*. A self-hosted Streamlit chat page was nonetheless added **after** v0.1 shipped
  (PRs #29–#31). It is deliberately constrained so it does not violate the architecture: it is an
  *additive channel* that calls the same `master.handle` seam via `web/turn.run_turn` (no bypass of
  classify → plan → govern → compose → enqueue), `streamlit` is an **optional** extra
  (`uv sync --extra web`, kept out of core deps), and it does not start the GP loop or vault watcher.
  It has no auth and no TLS by design (single-user home-LAN). Treat it as a v0.2-adjacent preview that
  landed early, not as v0.1 scope creep into the core.
- **Semantic recall location.** Spec wanted it as an `after_recall` event handler. It is computed
  inside `store.recall()` instead, because the event fires after context is already built. Documented
  deviation (Phase 20, ADR-0010).
- **`retry:repair` fitness is a structural proxy.** Offline held-out samples cannot induce real agent
  failures, so the retry-strategy target is scored structurally rather than behaviorally. It is the
  weakest fitness signal and is flagged as such (Phase 19, ADR-0007).
- **Speculative mode.** Implemented as cheap-leader-runs / strong-validates with peer fallback. The
  spec's "follow-up correction message in a later session if validation contradicts" is satisfied within
  the turn rather than as a cross-session proactive nudge (proactive output is v0.3).
- **Fan-out repair is peer-replacement only.** Sequential mode walks the full ladder; the five fan-out
  modes recover via a single peer substitution rather than cancel-and-retry inside `asyncio.gather`
  (cancel-and-retry there is genuinely ambiguous). Conscious narrowing, documented in Phase 13.

## Decisions and why (the ADR record)

The architectural "why" lives in `docs/adr/`. Sixteen ADRs, all Accepted:

- **0001 — Hand-rolled orchestration.** Plain Python + asyncio + an event bus, no framework. The whole
  system is small enough that a framework would add more surface than it removes.
- **0002 — Single-writer memory + queue.** Memory Agent is the only writer to durable state; every
  outbound message flows through `notification_queue`, even synchronous CLI replies. This is the seam
  v0.2 Telegram and v0.3 proactive jobs inherit without restructuring.
- **0003 — Master pipeline + six execution modes.** The fixed classify→plan→execute→govern→compose→enqueue
  pipeline and the strategy-per-mode runner.
- **0004 — Governance matrix + approval gate.** Risk/confidence/reversibility scored independently, combined
  by a priority-ordered rule matrix; `require_approval` is an interactive text gate.
- **0005 — Shell safety in `sandbox.py`, not `SKILL.md`.** Enforcement lives in code the LLM cannot rewrite;
  the skill body is only a prompt template. This is also a CLAUDE.md architectural rule.
- **0006 — GP self-improvement is approved, not autonomous.** The loop runs in the background but nothing
  promotes to production without explicit `/improvements` approval; the loop even boots paused.
- **0007 — Evolvable target *kinds* + config eval.** Targets are `prompt` or `config`; config variants are
  deterministic validated structural mutations (no LLM); the eval sandbox is side-effect-free.
- **0008 — Live swap via `active_evolutions`.** Approval writes one row; prompt assembly, routing, and
  tool-chain reads consult it, guarded so pure prompt assembly never bootstraps a DB.
- **0009 — Classifier determinism + routing completeness.** Defensive JSON parsing with a default fallback;
  `routing.yaml` always resolves to a workflow.
- **0010 — Semantic recall + lazy vec guard.** `sqlite-vec` loaded lazily; absent/blocked/disabled all
  degrade gracefully to recency-only.
- **0011 — Vault-sync polling + conflict queue.** A no-dependency polling daemon (no watchdog), content-hash
  echo suppression, conflicts queued rather than auto-merged.
- **0012 — Model-call envelope, typed directives, router-owned planning.** The post-v0.1 deepening:
  `agents/llm_run.py` (one envelope), `AgentDirectives` (typed replacement for the bare `metadata` dict),
  and `router.plan_workflow` returning a validated `WorkflowPlan`. Behavior-neutral refactors.
- **0013 — Self-authored skills: quarantine + approval boundary.** The self-extension experiment. Ubongo
  drafts new skills, but the boundary holds in code: quarantine before discoverability, a command-skill risk
  floor that is enforced not author-declared, static command validation reusing the sandbox contract, and a
  daemon that only drafts. The sandbox allowlist stays a human-only change (extends 0005, 0006).
- **0014 — Local-only observability (profiler).** `/profile` stats/cpu/mem are on-demand, stdlib-only,
  best-effort diagnostics over rows the runner already persists — a diagnostic view, not a telemetry pipeline.
- **0015 — MCP server as an additive channel.** Ubongo as an MCP server is the fourth front over the one
  `master.handle` seam; gated turns return `gated=true` and are never approvable over MCP (approval needs a
  human channel). LAN no-auth posture.
- **0016 — External tools behind one Connector seam.** The Connector agent is the only door out: MCP client
  sessions, model-planned calls, results as Findings. The first-class tool layer was deferred (not granted),
  the CLI bridge rejected (it would carve a network hole into the sandbox). Connector workflows score
  irreversible; turn risk escalates to the highest enabled server's declared risk.

Two CLAUDE.md rules worth restating because they constrained the build throughout: new capabilities default
to CLI scripts behind the constrained-bash skill rather than first-class tools, and new v0.2+ behavior ships
as handlers on the named events rather than edits to the pipeline.

## Parked and planned

- **v0.2 — Telegram.** The next real version. Additive on the existing event/queue seams: a new transport,
  a `before_send` policy handler, and the restored `allowed_user_ids` allowlist. The router, agents,
  governance, and evolution do not change. Out of v0.1 scope by design.
- **v0.2 — notification policy engine, quiet hours, holds, catch-up summarizer.** Deferred with Telegram.
- **v0.3 — proactive output.** The queue seam exists for it (flow starts at `enqueue`); nothing proactive
  is wired yet.
- **External integrations (Calendar, Gmail, Reddit, news).** v0.2+, one at a time, each a CLI script behind
  constrained-bash. `.env.example` already reserves the keys.
- **Architecture deepening — not pursued.** From the post-v0.1 roadmap
  ([Plans/05-09-architecture-deepening-roadmap.md](Plans/05-09-architecture-deepening-roadmap.md)):
  candidate **07** (VariantRow) was dropped because its premise (a key-mismatch bug) was disproven on
  inspection; candidate **09** (decompose the turn body) was judged speculative and left alone. Candidates
  01–06 and 08 merged.
- **Vault conflict resolution paths.** `keep-mine` / `keep-theirs` / `merge` exist in `/conflicts` for
  correctness, but with append-only daily notes the practical outcome is "coexist," so those paths are
  low-traffic by nature.
- **Web channel hardening.** The Streamlit UI is intentionally unauthenticated for home-LAN use. Any move
  toward exposure would need auth/TLS and is not planned.

## Where to look

- Phase-by-phase changelog and acceptance checklist: [STATUS.md](STATUS.md)
- The contract for v0.1 scope: [UBONGO_BUILD.md](UBONGO_BUILD.md)
- Decisions with rationale: [docs/adr/](docs/adr/) (0001–0016)
- Living architecture (C4 + glossary): [docs/architecture/](docs/architecture/), [CONTEXT.md](CONTEXT.md)
- Security contract and its known v0.1 limits: [docs/SECURITY.md](docs/SECURITY.md)
- Cumulative manual smoke playbook: [tests/manual/smoke_test.md](tests/manual/smoke_test.md)
</content>
</invoke>
