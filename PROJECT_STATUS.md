# Ubongo — Status Briefing

*2026-06-12. Grounded in the tree: 960 pytest tests collected green, ~15,400 LOC under `src/ubongo/`, `main` at v0.1.5.*

## Where we are against the plan

The plan is [UBONGO_BUILD.md](UBONGO_BUILD.md): 22 phases across six tiers, one branch per phase. **All 22 phases are complete, merged, and certified (2026-06-04), with all 26 acceptance criteria met.** The project is now past its own plan: five post-v0.1 increments have landed on `main` (web UI v0.1.1, self-authored skills v0.1.2, profiler + service control v0.1.3, MCP server channel v0.1.4, MCP client / Connector agent v0.1.5), plus two architecture-deepening refactor passes. The next planned version, v0.2 (Telegram), has not started. So the honest answer is: v0.1 done, v0.2 not begun, and the gap filled with channels, observability, and self-extension work the v0.1 spec listed as out of scope — a drift STATE.md documents openly.

## What is implemented and working

Everything shipped is exercised, not just present. The 960-test suite covers roughly one test module per source module, and the cumulative manual smoke playbook ([tests/manual/smoke_test.md](tests/manual/smoke_test.md)) was run end-to-end at certification. Working today: the full turn pipeline (classify → plan → execute → govern → compose → enqueue → memory) with ten-plus worker agents and all six execution modes; the Repair Agent's recovery ladder in sequential mode; the governance matrix with its interactive approval gate and the hardened shell sandbox; the GP self-improvement loop end to end (generation, sandboxed fitness over 33 held-out conversations, human-approved promotion with live swap); semantic recall via `sqlite-vec`, the vault-link graph, bidirectional vault sync with a conflict queue, and the unified audit; four channels (REPL, one-shot, Streamlit web, MCP server) all over the single `channel.run_turn` seam; skill self-authoring with quarantine and a manual approval gate; the local profiler, service-control scripts, and a CI pipeline with an automated smoke gate.

## What is missing or unfinished

**(a) Planned but not built.** v0.2 Telegram (transport, `before_send` policy handler, restored `allowed_user_ids`) — the seams exist, nothing started. With it: the notification policy engine, quiet hours, and catch-up summarizer. v0.3 proactive output. External integrations (Calendar, Gmail, Reddit, news) — `.env.example` reserves keys only.

**(b) Partial or stubbed.** The openspec change ledger is stale: `openspec/changes/complete-fanout-peer-replacement/` claims three fan-out modes lack peer-replacement repair, but the code has it wired in all five modes with per-mode tests (`runner.py:568,636,753,881,1009`, `tests/test_runner.py`) — the change is implemented but never archived. The `retry:repair` evolution target is scored by a structural proxy, acknowledged as the weakest fitness signal. The Connector agent is opt-in only (`/mode connector_session`, never auto-routed) with per-call gating and tool allowlists explicitly deferred. Deepening candidates 09, 16, and 19 were deliberately dropped or parked.

**(c) Known gaps.** Web and MCP channels have no auth/TLS (deliberate home-LAN posture, but a hard limit on exposure). MCP-driven turns cannot approve gated actions — machine callers stall on anything requiring approval. The ~15,000 LOC soft budget has been crossed for the first time (~15,400); the project's own stated remedy is to cut, and v0.2 should be a transport, not a subsystem. PR #19 (mutation testing) is parked on purpose, not abandoned. Minor doc rot: STATE.md says "twelve ADRs"; there are sixteen.

*Everything above was verified against tests, docs, and git log today except the two items flagged as inferred.*
