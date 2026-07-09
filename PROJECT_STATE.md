# Ubongo — State of Project Briefing

*Written 2026-06-20 for a strategy conversation about what to build next. Ground truth checked against the tree: `main` is at v0.5.7 (trust protocol complete); the active branch is `v0.6/00-streaming-seam` (PR #56, ready, unmerged). ~17,393 LOC under `src/ubongo/`, 1,057 tests collected, 23 accepted ADRs.*

## Status

### Where we are against the plan

Ubongo has run through three plans. **v0.1** (the original 22-phase CLI build) is certified and on `main`, plus a post-v0.1 layer (web UI, self-authored skills, profiler, MCP server, MCP client/Connector). **v0.5 — the trust protocol** ([Plans/v0.5-trust-protocol.md](Plans/v0.5-trust-protocol.md)) is **complete and merged**: all eight phases (00 reconcile, 01 outer envelope, 02 store split, 03 approval seam, 04 Telegram, 05 grants, 06 standing jobs, 07 contract/identity) are on `main`, version `0.5.7`. The third and current plan is **v0.6 — the live console** ([Plans/v0.6-live-console.md](Plans/v0.6-live-console.md)), a six-phase line for a streaming browser UI; **Phase 00 (the streaming seam) is built and PR #56 is ready for review but not yet merged.** So: v0.5 done, v0.6 just opened, one phase in flight.

Versions ride the branch name (`v0.X/NN-name` → `0.X.NN`), bumped by CI on merge. An earlier non-monotonic blip (`0.5.5 → 0.5.4` when phase 04 merged after 05) self-corrected once 0.5.6/0.5.7 landed; a guard against backward version derivation is an open follow-up.

### What's implemented and working

Exercised by the 1,057-test suite (roughly one module per source module). By the latest layers:

- **The full turn pipeline** ([master.py](src/ubongo/master.py)): classify → plan → execute → govern → compose → commit → enqueue, ten-plus worker agents, all six execution modes; peer-replacement repair across all five fan-out modes.
- **The trust protocol (v0.5), all live**: the resumable approval seam (`test_approval_seam.py`, `test_repl_approval.py` — a gated turn persists a `pending_approvals` record, resolvable from any channel via `master.resume_approval`); the grant registry (`test_grant_registry.py` — standing `connector:<server>` consent, a governance rule *after* the safety rules); **standing jobs** (`test_jobs_state.py`, `test_standing_jobs.py` — a fourth `DaemonLoop` runs scheduled turns through `master.handle`, parks-and-raises on a missing grant, default-deny quiet-hours/TTL); **verbosity per domain** (`test_governance_verbosity.py` — a `governance.yaml` knob threaded into the composer persona as one line); **backup** (`test_backup.py` — `ubongo backup` writes a secret-free portable archive; restore re-arms grants).
- **The v0.6 streaming seam** (`test_stream_bridge.py`, 4 tests): per-turn event streaming — `web/console/stream_bridge.py` runs `channel.run_turn` on a background thread and forwards the named pipeline events to the browser over SSE, single-flight, with a terminal frame and handler cleanup even on exception. Exercised directly (no HTTP); the live browser stream is manual.
- **Six channels** over one `channel.run_turn` seam: REPL, one-shot, web (Streamlit), MCP server, Telegram, and now the streaming console — all thin adapters, all behind optional extras except the CLI.
- **Memory, GP loop, authoring loop, vault sync** — all from earlier tiers, all still green.

### What's missing or unfinished

**(a) Planned but not built.** v0.6 Phases 01–05: the live **agent roster** panel (the headline), the **activity stream + response render**, **approval + sources** panels, **optional token-streaming** (the only phase touching `llm.py`), and **retiring Streamlit**. The console today is a bare event log, not the rich UI the plan describes.

**(b) Partial or stubbed.** The console (Phase 00) is feature-complete *as a transport* but unmerged (PR #56). Standing jobs' Connector-backed news digest is config-shipped but **disabled** (needs a real MCP news server; live path is Pi-only). The Connector stays opt-in (`/mode connector_session`), server-granular grants only. Verbosity is manual-only — the GP-evolvable `verbosity:<domain>` target is deferred.

**(c) Known gaps and constraints.** No channel has auth/TLS in-app (LAN/private-relay posture); the console is explicitly single-flight (no concurrent turns). The egress envelope and the live relays are enforcement *outside* the test suite. A full multi-agent governed turn is **slow for voice** — any telephony channel (Vapi/Twilio, discussed but not planned) would need a fast persona-only route plus token-streaming. LOC is ~17,400, well past the ~15,000 soft target; the project's "cut, don't expand" rule is now chronically strained, and Phase 05 (retire Streamlit) is the one scheduled clawback.

## Architecture

### Component map and data flow

```text
REPL / one-shot / web / MCP / Telegram / console      (presentation only, six channels)
        └── channel.py  — bootstrap() + run_turn(): the no-bypass envelope
              └── master.py — classify → plan → execute → govern → compose → commit → enqueue
                    ├── classifier.py     tone/intent (deterministic fallback)
                    ├── router.py         plan_workflow(): routing.yaml + hysteresis + /mode → WorkflowPlan
                    ├── runner.py         WorkflowRunner: six execution-mode strategies (largest module)
                    ├── governance/       risk/confidence/reversibility matrix; approval.py (resumable);
                    │                     grants.py (post-safety grant rule); verbosity.py (length knob)
                    ├── agents/           workers; composer=True selects user text; llm_run.py = one
                    │                     model-call envelope; connector.py = the only door out (mcp/client.py)
                    ├── memory/           store + trace + evolution_state/authoring_state/index_state/
                    │                     grant_state/jobs_state (single writer = Memory Agent); vault, embeddings, graph
                    └── delivery/         notification_queue — every outbound message, even sync replies
```

Supporting + newer subsystems: `events.py` (the named-event bus — the hook surface, and what the console's `stream_bridge.py` forwards), `daemon.py` (one `DaemonLoop` behind four daemons: GP, authoring, vault-watch, **jobs**), `jobs/` (standing jobs: loop, runner, policy, delivery, commands), `web/console/` (the streaming channel: `stream_bridge.py` HTTP-free core + `app.py` FastAPI), `backup.py` (portability), `sandbox.py` (all shell safety), `evolution/`, `authoring/`, `mcp/`, `telegram/`.

Extension points, few and deliberate: new channels adapt over `channel.run_turn`; new behavior registers handlers on the named events (this is literally how the console streams and how standing jobs would hook delivery); new tools default to CLI scripts behind constrained-bash; external services go through the Connector only; evolvable behavior is addressed by target strings; approvals resolve the one `pending_approvals` record.

### Stack

Python 3.11+ under uv. LiteLLM over OpenRouter for every model call (one `complete()` chokepoint). Stdlib SQLite + `sqlite-vec` (lazy; degrades to recency-only recall). PyYAML config, secrets in `.env`. Optional, lazily-imported extras, each gating one channel: `streamlit` (web), `mcp` SDK, `httpx` (Telegram), **`fastapi`+`uvicorn` (console)**. **Deliberately absent**: LangGraph/Temporal/Ray/Redis/Docker-in-app — hand-rolled asyncio + an event bus. Non-code but load-bearing: the deployment envelope (rootless Podman + nftables egress, Linux/Pi only) is the real network boundary.

## Decisions and constraints

Twenty-three accepted ADRs in [docs/adr/](docs/adr/). The ones that close off paths:

- **0001 Hand-rolled orchestration** — no framework; new complexity paid in plain Python.
- **0002 Single writer + queue** — Memory Agent is the only durable writer; every outbound message flows through `notification_queue`. This is the seam standing jobs (proactive output) finally used.
- **0005 Shell safety in `sandbox.py`** — enforcement in code the LLM can't rewrite; reaffirmed by 0016 (rejected a CLI bridge for MCP).
- **0006 + 0013 The human approval boundary** — GP loop and authoring daemon boot paused, never auto-promote; the sandbox allowlist is human-only. 0022 leans on this so a future *learned* verbosity is safe by construction.
- **0016 Connector is the one external door** — first-class tools deferred; connector workflows irreversible, risk per server.
- **0017 The outer envelope**, **0018 resumable approval seam**, **0019 grant registry** — the trust spine: egress control below the app, approve-anywhere, standing consent after the safety rules.
- **0020 Telegram**, **0021 standing jobs**, **0022 contract/identity**, **0023 the live console** — the recent additions: a cloud channel, proactive output (with quiet-hours/TTL default-deny), verbosity-as-config + backup-is-data, and per-turn event streaming (single-flight, LAN no-auth).

Two CLAUDE.md rules bound everything: new capabilities default to CLI scripts behind constrained-bash, and new behavior ships as event handlers, not pipeline edits.

### The core invariant

**Every consequential action passes through exactly one governed seam, and nothing changes the system itself — or reaches a new external capability — without explicit human approval.** Turns through `master.handle`; durable writes through the Memory Agent; outbound through the queue; external calls through the Connector; shell through `sandbox.py`; approvals through the one `pending_approvals` record; self-modification and first external reach through quarantine/grant + a human gate. The console reinforces this by design: it *observes* the bus and *resumes* approvals; it never opens a second path. A feature that bypasses any of these, or lets a loop promote its own output, violates the project's reason for being.

## Open ground

1. **The v0.6 fork is now: roster (Phase 01) is the headline, and it's where Ubongo beats the reference UIs.** Because the agent lineup is planned up front (`WorkflowPlan`), the console can show "working now / queued / retiring" as fact, not a guess — unlike a dynamic-spawn coding agent. This is the highest-leverage, lowest-risk console build, and the transport (Phase 00) already exists. **But the immediate next build has been re-prioritized:** Giuseppe has chosen to insert the **Signal channel** (a new **v0.7** line, [Plans/signal-channel.md](Plans/signal-channel.md), [ADR-0024](docs/adr/0024-signal-channel.md) Proposed) *ahead of* v0.6/01-05 — the privacy-respecting messaging channel he prefers over Telegram, an additive adapter in the Telegram pattern whose one difference is a locally-run signal-cli sidecar over JSON-RPC. Sequencing: merge #57 (ForgeLoop) + #56 (streaming seam), build v0.7/00-01, then resume v0.6/01. *(Decision recorded 2026-07-09; docs-only, nothing built.)*

2. **Voice/telephony is the live question behind recent conversation.** A Vapi or Twilio voice channel would be a seventh additive adapter (Ubongo stays telephony-free), but it collides with latency: a full governed multi-agent turn is too slow to speak in real time. It needs a *fast route* (persona-only, skip evaluator/critic) and the token-streaming work (v0.6 Phase 04) to start talking before the turn finishes. WhatsApp, by contrast, is a *messaging* channel — the Telegram pattern over Twilio/Meta Cloud API, cheap and low-risk. *(Inferred from this session's discussion; nothing built.)*

3. **The LOC budget is chronically over and the only scheduled cut is Phase 05.** At ~17,400 (~16% over target), every new channel widens the gap. The discipline that held through v0.5 (transports, not subsystems; optional extras) is under more pressure now that the console *is* a subsystem.

4. **Single-flight is a real console limitation.** One turn at a time, no correlation. Fine for one user; it's the thing that will bend first if the console ever wants to watch the GP loop or serve two tabs (the `contextvar` variant is named but deferred).

5. **Enforcement keeps drifting outside the suite.** The egress envelope, the live relays (Telegram/console SSE), and now voice would all be real boundaries pytest can't see. "Green suite" certifies less of the actual trust/operational posture than it did at v0.1.

6. **The self-modification loops still boot paused, and whether they're exercised in practice is not visible from the tree** — the self-improving premise (the project's identity) may be dormant. A product question before a technical one. *(Inferred: the tree shows machinery + tests, not usage.)*

*Inference flags: Status/Architecture/Decisions verified against the tree, tests, ADRs, and git log on 2026-06-20. Inferred and flagged inline: the voice/WhatsApp direction (this session's discussion, unbuilt) and the self-modification loops' real-world usage.*
