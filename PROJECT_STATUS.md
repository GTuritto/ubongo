# Ubongo — Status Briefing

*2026-06-20. `main` is at v0.5.7; the active branch is `v0.6/00-streaming-seam` (PR #56, ready, unmerged). 1,057 tests collected. Fast catch-up, not an architecture doc.*

## Where we are against the plan

Two plans behind us, one active. **v0.1** (22-phase CLI build) is certified and on `main`. **v0.5 — the trust protocol** is **complete and merged**: all eight phases (00 reconcile, 01 outer envelope, 02 store split, 03 approval seam, 04 Telegram, 05 grants, 06 standing jobs, 07 contract/identity) are on `main` at version `0.5.7`. The active plan is **v0.6 — the live console** ([Plans/v0.6-live-console.md](Plans/v0.6-live-console.md)), a six-phase line for a streaming browser UI. **Phase 00 (the streaming seam) is built and PR #56 is ready for review, not yet merged.** So: v0.5 done, v0.6 just opened, one phase in flight, five phases (01–05) ahead.

## What's implemented and working

Exercised by the 1,057-test suite (roughly one module per source module). The recent layers all carry tests:

- **Trust protocol (v0.5), all live**: the resumable approval seam (`test_approval_seam.py`, `test_repl_approval.py`) — a gated turn persists a record, resolvable from any channel; the grant registry (`test_grant_registry.py`) — standing `connector:<server>` consent checked after the safety rules; **standing jobs** (`test_jobs_state.py`, `test_standing_jobs.py`) — a fourth background daemon runs scheduled turns through `master.handle`, parks-and-raises on a missing grant, with quiet-hours hold + raise-TTL default-deny; **verbosity per domain** (`test_governance_verbosity.py`) — a `governance.yaml` knob threaded into the persona; **backup** (`test_backup.py`) — `ubongo backup` writes a secret-free archive, restore re-arms grants.
- **v0.6 streaming seam** (`test_stream_bridge.py`, 4 tests): per-turn event streaming — the turn runs on a background thread and its pipeline events forward to the browser over SSE, single-flight, with a terminal frame and handler cleanup even on a turn exception. Exercised directly (no HTTP); the live browser stream is manual.
- **Six channels** over one `channel.run_turn` seam: REPL, one-shot, web (Streamlit), MCP, Telegram, and the new streaming console — all behind optional extras except the CLI.
- Everything from earlier tiers (full turn pipeline, six execution modes, repair ladder, GP + authoring loops, semantic recall, vault sync) remains green.

## What's missing or unfinished

**(a) Planned but not built.** v0.6 Phases 01–05: the live **agent roster** panel (the headline feature), the **activity stream + response render**, **approval + sources** panels, **optional token-streaming** (the only phase touching `llm.py`), and **retiring Streamlit**. The console today is a bare event log, not the rich UI the plan describes.

**(b) Partial or stubbed.** The console (Phase 00) is complete *as a transport* but unmerged. Standing jobs' news-digest example is config-shipped but **disabled** (needs a real Connector MCP news server; the live path is Pi-only). The Connector stays opt-in (`/mode connector_session`), grants are server-granular only, and verbosity is manual-only (the GP-evolvable `verbosity:<domain>` target is deferred).

**(c) Known gaps and constraints.** No channel has in-app auth/TLS (LAN/private-relay posture); the console is single-flight (no concurrent turns). The egress envelope and the live relays are enforcement *outside* the test suite. A full multi-agent governed turn is slow for real-time voice — a telephony channel (Vapi/Twilio, discussed this session, not built) would need a fast persona-only route plus token-streaming. LOC is ~17,400, ~16% over the ~15,000 soft target; Phase 05 (retire Streamlit) is the one scheduled clawback. *(Inferred: the voice direction is from this session's discussion; nothing is built.)*

*Verified against the test inventory, git log, and the plan docs on 2026-06-20. The single inference (voice/telephony) is flagged above.*
