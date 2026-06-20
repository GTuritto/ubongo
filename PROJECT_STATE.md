# Ubongo — State of Project Briefing

*Written 2026-06-20 for a strategy conversation about what to build next. Ground truth checked against the tree on branch `v0.5/04-telegram`: 1,010 tests collected, ~16,060 LOC under `src/ubongo/`, 20 accepted ADRs. `main` is at v0.5.5.*

## Status

### Where we are against the plan

There have been two plans. The first, [UBONGO_BUILD.md](UBONGO_BUILD.md), defined v0.1 as 22 phases across six tiers; it is **done and certified (2026-06-04)**, followed by an accreted post-v0.1 layer (web channel, self-authored skills, profiler, MCP server, MCP client/Connector). The second, and the one that matters now, is the **v0.5 trust-protocol plan** ([Plans/v0.5-trust-protocol.md](Plans/v0.5-trust-protocol.md)): an eight-phase arc (00–07) that hardens Ubongo for a cloud-relayed messaging channel and standing autonomous jobs. It ships as the `v0.5.x` line, version derived from the phase branch name in CI.

Phases built and merged to `main`: **00** (reconcile the ledger — the stale fan-out-repair proposal that misled prior reviews is archived), **01** (the outer envelope: rootless Podman quadlets + UID-keyed nftables egress allowlist, Linux/Pi only), **02** (split the 1,990-line `store.py` into five table-family modules + shared judgment parsing), **03** (the typed, persisted, resumable approval seam), and **05** (the grant registry + the Connector armed). Note the non-numeric build order: 05 merged before 04.

In flight: **Phase 04 — Telegram**, the channel proper. It is committed on the current branch with full tests but **not yet merged** — the phase PR is still open per the branch-per-phase workflow. *(Inferred from log shape; confirm with `gh pr status`.)*

Not started: **Phase 06** (standing jobs — the proactive-output seam, the old v0.3) and **Phase 07** (the contract and identity — backup/portability + a verbosity-per-domain governance knob, deliberately last and partly designed-deferred).

### What is implemented and working

Exercised by the 1,010-test suite (roughly one module per source module, plus REPL, live-swap, recovery, evaluation, sync, audit, authoring, and now approval/grant/telegram suites):

- **The full turn pipeline** ([master.py](src/ubongo/master.py)): classify → plan → execute → govern → compose → commit → enqueue, with ten-plus worker agents and all six execution modes; peer-replacement repair wired into all five fan-out modes plus the full ladder in sequential.
- **Governance** with a five-rule risk/confidence/reversibility matrix, the hardened shell sandbox, and — new in v0.5 — a **resumable approval seam** (`test_approval_seam.py`, `test_repl_approval.py`): a gated turn writes a `pending_approvals` record and can be approved from any channel via `master.resume_approval`.
- **The grant registry** (`test_grant_registry.py`): standing per-server consent for `connector:<server>`, checked by a governance rule *after* the safety rules; grant-on-approval, `/grants` + `ubongo grants`, revocation that re-arms the ask and survives restart.
- **Five channels** over one `channel.run_turn` seam: REPL, one-shot, web (Streamlit), MCP server, and Telegram (`test_telegram_service.py`/`test_telegram_bot.py` — network-free core + httpx long-poll, both behind the optional `httpx` extra).
- **The GP self-improvement loop** and **the authoring loop**, both background daemons that boot paused, with human-approved promotion (live swap via `active_evolutions`) and human-approved skill materialization (quarantine → `/skill-candidates`).
- **Memory**: SQLite canonical (single writer = Memory Agent), Markdown vault projection, `sqlite-vec` semantic recall, the `[[wikilink]]` graph, bidirectional vault sync with a conflict queue, unified audit.
- **The outer envelope** ([deploy/envelope/](deploy/envelope/)): Containerfile, quadlets, nftables config, egress-refresh timer — real and deployed, but enforced *outside* `src/` and **not** covered by pytest.

### What is missing or unfinished

**(a) Planned but not built.** Phase 06 (standing/proactive jobs) and Phase 07 (trust contract, backup/identity, verbosity knob). The notification-policy engine (quiet hours, holds, catch-up summarizer) rides with them. External integrations (Calendar, Gmail, Reddit, news) — `.env.example` reserves keys; the Connector is the governed path they would use, but none are built.

**(b) Partial or stubbed.** Telegram is feature-complete with tests but **unmerged** — ready for review, not shipped on `main`. The Connector is opt-in only (`/mode connector_session`, never auto-routed); per-tool pre-execution gating and tool-level allowlists are deferred (grants are server-granular). The `retry:repair` evolvable target was **deleted** in Phase 05 (its fitness was a structural proxy) — so that prior weak-signal item is resolved by removal, not improvement.

**(c) Known gaps and constraints.** Approval has **no expiry, escalation, or quiet-hours policy** — a `pending_approvals` row can sit forever; harmless today, a real gap the moment Phase 06 lets a proactive job gate a turn with no human in any loop. The egress envelope is enforcement the test suite cannot see, so a change to what the Connector or a CLI script reaches passes pytest yet may be silently blocked/allowed on the box; and it is Linux-only by design — macOS has no equivalent. Trust posture stays single-user: Telegram auth is a flat `allowed_user_ids` allowlist (empty = deny all), no per-request auth or TLS in the app. LOC is ~16,060, ~7% over the ~15,000 soft target.

## Architecture

### Component map and data flow

```
REPL / one-shot / web / MCP server / Telegram      (presentation only)
        └── channel.py  — bootstrap() + run_turn(): the no-bypass envelope
              └── master.py — classify → plan → execute → govern → compose → commit → enqueue
                    ├── classifier.py     tone/intent (deterministic fallback)
                    ├── router.py         plan_workflow(): routing.yaml + hysteresis + /mode → WorkflowPlan
                    ├── runner.py         WorkflowRunner: six execution-mode strategies (largest module, 1,121 ln)
                    ├── governance/       risk/confidence/reversibility matrix; approval.py (resumable seam);
                    │                     grants.py (post-safety grant rule)
                    ├── agents/           workers; composer=True selects user-facing text; llm_run.py is the one
                    │                     model-call envelope; connector.py is the only door out (via mcp/client.py)
                    ├── memory/           store.py + trace.py + evolution_state/authoring_state/index_state/grant_state
                    │                     (single writer = Memory Agent); vault.py, embeddings.py, graph.py, vault_watch.py
                    └── delivery/         notification_queue — every outbound message, even sync replies
```

Supporting machinery: `events.py` (named-event bus; the hook surface for new behavior), `commands.py` (slash-command registry), `invoke.py` (shared agent-invocation core), `llm.py` (LiteLLM wrapper: one retry, token accounting), `sandbox.py` (all shell safety, in code the LLM cannot rewrite), `daemon.py` (one `DaemonLoop` behind the three daemons), `evolution/`, `authoring/`, `profiling.py`, `mcp/` (server + client, the only modules importing the optional MCP SDK), `telegram/`, `web/`.

The extension points are few and deliberate: new channels adapt over `channel.run_turn`; new behavior registers handlers on named events; new tools default to CLI scripts behind constrained-bash; external services go through the Connector only; evolvable behavior is addressed by target strings the live-swap read paths consult; new approval surfaces resolve the one `pending_approvals` record.

### Stack

Python 3.11+ under uv. LiteLLM over OpenRouter for all model calls (one `complete()` chokepoint). Stdlib SQLite + `sqlite-vec` (lazily loaded; degrades to recency-only recall). PyYAML config, secrets in `.env`. Optional, lazily-imported extras: `streamlit` (web), `mcp` SDK (both MCP directions), `httpx` (Telegram). **Deliberately absent**: LangGraph/Temporal/Ray/Redis/Docker-in-app — hand-rolled asyncio + event bus. Load-bearing-but-quiet: LiteLLM (one provider seam for everything) and `sqlite-vec` (embeddings inside the canonical file, which is why "memory" is one component). New for v0.5: the deployment envelope (Podman + nftables) is a non-code dependency that is now the real network boundary.

## Decisions and constraints

Twenty accepted ADRs in [docs/adr/](docs/adr/). The ones that close off paths — which is what a what-next conversation needs:

- **0001 Hand-rolled orchestration** — no framework; new orchestration complexity is paid in plain Python.
- **0002 Single writer + queue** — Memory Agent is the only durable writer; every outbound message flows through the queue. Writing memory or sending output directly is wrong by construction. This is the seam Phase 06 proactive jobs inherit.
- **0005 Shell safety in `sandbox.py`** — enforcement in code the LLM cannot rewrite; ADR-0016 reaffirmed it by *rejecting* a CLI bridge for MCP.
- **0006 + 0013 The human approval boundary** — neither the GP loop nor the authoring daemon promotes anything autonomously; both boot paused; the sandbox allowlist is a human-only change. Phase 07's "learned legibility" is explicitly constrained by this: the dangerous half of learning one's own boundaries is structurally impossible here.
- **0008 Live swap via `active_evolutions`** — evolution behavior change is one DB row, trivially rolled back.
- **0016 External tools behind one Connector seam** — the first-class tool layer was *deferred, not granted*; the CLI bridge *rejected*. Connector workflows score irreversible; turn risk escalates to the highest enabled server's declared risk.
- **0017 The outer envelope** — egress control below the application, Linux-only; what makes the cloud relay acceptable.
- **0018 The resumable approval seam** — approval is a persisted record with one re-issue path; the prerequisite for approve-later and cross-channel approval.
- **0019 The grant registry** — standing consent checked *after* the safety rules (a grant can never auto-proceed something the matrix would reject); paired with the `retry:repair` cut.
- **0020 Telegram** — the first cloud-relayed channel, additive over the one seam; auth via `allowed_user_ids`.

Two CLAUDE.md rules bound everything: new capabilities default to CLI scripts behind constrained-bash, and new behavior ships as event handlers, not pipeline edits.

### The core invariant

**Every consequential action passes through exactly one governed seam, and nothing changes the system itself — or reaches a new external capability — without explicit human approval.** Turns go through `master.handle` via the channel core; durable writes through the Memory Agent; outbound through the queue; external calls through the Connector; shell through `sandbox.py`; approval through the one `pending_approvals` record; self-modification and first external reach through quarantine/grant plus a human gate. A new feature that opens a second path around any of these, or lets a loop promote its own output, violates the project's reason for being.

## Open ground

This is where the strategy conversation has room:

1. **Merge Telegram, then the fork is real: Phase 06 (standing jobs) vs. Phase 07 (contract/identity).** Phase 04 is the last channel the plan names; once merged, the remaining arc is proactive output and then portability. Phase 06 is the higher-leverage and higher-risk move — it is the first time Ubongo speaks unprompted.

2. **Proactive output collides head-on with the approval gap.** The resumable seam (0018) assumes someone eventually answers; a standing job that triggers a `require_approval` turn at 3am with no human present has nowhere to go. Phase 06 cannot ship without an expiry/escalation/quiet-hours posture (default-deny or auto-expire) that does not exist today. This is the single most important unresolved design question, and it is squarely in front of the next phase.

3. **The Connector is deliberately half-armed, and the integrations that would arm it are the obvious next product pull.** No auto-routing, no per-call pre-execution gating, server-granular grants only. The moment Calendar/Gmail become real, per-tool granularity (deferred in 0016/0019) comes due — and it interacts with #2, because a proactive job using a granted connector is exactly the no-human-present case.

4. **The LOC budget is spent and the named fracture is already fixed.** The store split (Phase 02) cashed the one trigger-parked deepening candidate; there is no obvious next cut. At ~7% over, Phase 06/07 must be net-light or paired with a cut. "Standing jobs" is a subsystem, not a transport — the first thing in a while that genuinely pushes the budget.

5. **The self-modification loops boot paused, and whether they are actually exercised is not visible from the tree.** If promotions and authored skills are not happening in practice, the self-improving premise — the project's identity — is dormant, which is a product question before a technical one. *(Inferred: the tree shows machinery and tests, not usage.)*

6. **Enforcement is increasingly outside the test suite.** The egress envelope and the Telegram relay are real boundaries that pytest cannot see, and Phase 07's backup/portability story leans on the Phase 02 layering rather than code. As the system grows operational surface, "green suite" certifies less of the actual trust posture than it used to.

*Inference flags: Status/Architecture/Decisions verified against the tree, tests, ADRs, and git log on 2026-06-20. Two inferred claims, both flagged inline: Phase 04's PR is still open (from log shape), and the self-modification loops' real-world usage (not derivable from the repo).*
