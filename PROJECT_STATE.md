# Ubongo — State of Project Briefing

*Written 2026-06-12 for a strategy conversation about what to build next. Ground truth checked against the tree: 960 pytest tests collected green, 15,396 LOC under `src/ubongo/`, `main` at v0.1.5.*

## Status

### Where we are against the plan

The plan is explicit: [UBONGO_BUILD.md](UBONGO_BUILD.md) defines v0.1 as 22 phases (0–21) across six tiers, each built on its own `phase-N-<name>` branch and merged by the user. **All 22 phases are done and certified (2026-06-04); all 26 acceptance criteria met.** Since then a post-v0.1 layer has accreted on `main`, version-stamped v0.1.1 through v0.1.5: a Streamlit web channel, self-authored skills (the `authoring/` package), a local profiler + service control, an MCP *server* channel (Ubongo as a server), and an MCP *client* (the Connector agent — Ubongo consuming external servers). Two architecture-deepening refactor passes also landed (candidates 01–06/08, then 14/15/17/18).

So the project sits in an interstitial phase the plan does not name: v0.1 is finished, v0.2 (Telegram) has not started, and the gap has been filled with channels, observability, and self-extension work that the v0.1 spec explicitly listed as out of scope. [STATE.md](STATE.md) is candid about this drift and is itself fresh (dated today); it is the best companion document to this one.

### What is implemented and working

Everything below is exercised by the 960-test pytest suite (roughly one test module per source module, plus REPL, live-swap, recovery, evaluation, sync, audit, and authoring suites) and by a cumulative manual smoke playbook ([tests/manual/smoke_test.md](tests/manual/smoke_test.md)) that was run end-to-end at certification:

- **The full turn pipeline**: classify → plan → execute → govern → compose → enqueue → memory commit, with ten-plus registered worker agents and all six execution modes (sequential, parallel, competitive, collaborative, debate, speculative).
- **Self-healing**: a Repair Agent with a seven-category failure taxonomy and an ordered recovery ladder in sequential mode; write-buffer rollback on failure.
- **Governance**: a five-rule risk/confidence/reversibility decision matrix from `governance.yaml`, an interactive y/n/why approval gate, and a hardened shell sandbox (allowlist, no metacharacters, no traversal, empty child PATH, 10s timeout).
- **The GP self-improvement loop**: variant generation over prompt and config targets, sandboxed fitness evaluation against 33 held-out conversations, an autonomous-but-paused background loop, and human-approved promotion with live swap via `active_evolutions`.
- **Memory**: SQLite canonical store, Markdown vault projection, `sqlite-vec` semantic recall folded into turn context, a `[[wikilink]]` graph, bidirectional vault sync via a polling watcher with a conflict queue, and a unified audit file.
- **Four channels** over one seam: REPL, one-shot, web (Streamlit, optional extra), and MCP server (stdio + streamable HTTP), all thin adapters over `channel.run_turn`.
- **Self-extension**: `/author` plus an authoring daemon that drafts new skills into quarantine; approval (`/skill-candidates`) is the only path to live.
- **Operations**: a stdlib-only profiler (`/profile` stats/cpu/mem), `ubongo-ctl.sh`, systemd units, a CI pipeline with an automated smoke gate, and version-driven releases.

### What is missing or unfinished

**(a) Planned but not built** — the roadmap beyond v0.1, none of it started:
- v0.2 Telegram: a new transport, a `before_send` policy handler, restored `allowed_user_ids` auth. The seams (channel core, event bus, notification queue) exist and were built for exactly this.
- v0.2 notification policy engine, quiet hours, holds, catch-up summarizer.
- v0.3 proactive output (the queue seam exists; nothing proactive is wired).
- External integrations (Calendar, Gmail, Reddit, news) — `.env.example` reserves keys; nothing implemented. The Connector agent now provides the governed path these would use.

**(b) Partial or stubbed:**
- **The openspec ledger is out of sync with the code.** `openspec/changes/complete-fanout-peer-replacement/` proposes wiring peer-replacement repair into competitive, debate, and speculative modes — but the code already has it: peer-only recovery is wired into all five fan-out modes (`runner.py:568,636,753,881,1009`), with dedicated tests for peer replacement and the unrecoverable case in each mode in `tests/test_runner.py`. The change is implemented but never archived; the remaining work is bookkeeping, not code.
- **`retry:repair` fitness is a structural proxy**, not behavioral — offline samples can't induce real failures. Flagged in ADR-0007 as the weakest evolution signal.
- **The Connector agent is opt-in only** (`/mode connector_session`, never auto-routed), and per-tool pre-execution gating was explicitly deferred (ADR-0016): a high-risk server's *result* needs approval, but individual calls are not gated before they happen.
- **Deepening candidates deliberately not done**: candidate 16 dropped, candidate 19 (split a growing `store.py`) trigger-parked, candidate 09 (decompose the turn body) judged speculative, candidate 07 dropped after its premise was disproven.
- Speculative mode's "cross-session correction message" is satisfied within-turn only (proactive output is v0.3).

**(c) Known gaps, accepted by design but real:**
- The web and MCP channels have **no auth and no TLS** (single-user home-LAN posture, ADR-0015). Any exposure ambition reopens this.
- MCP-driven turns **cannot approve gated actions** — a `require_approval` turn returns `gated=true` and stalls until a human channel approves. Correct, but it means machine callers hit a wall on anything risky.
- What leaves the machine via Connector tool arguments is whatever the Connector's model plans — documented in SECURITY.md as a real consideration, controlled only by per-server `risk`/`enabled` flags.
- **The LOC soft budget (~15,000) has been crossed for the first time** (~15,400). STATE.md's own verdict: acceptable for the MCP work, but the budget is spent; v0.2 should add a transport, not another subsystem.
- PR #19 (mutation testing) is parked for hardware reasons — open by intent, not forgotten.
- Minor doc rot: STATE.md says "twelve ADRs"; there are sixteen.

## Architecture

### Component map and data flow

A turn flows through one seam, regardless of channel:

```
REPL / one-shot / web / MCP server          (presentation only)
        └── channel.py  — bootstrap() + run_turn(): the no-bypass envelope
              └── master.py — classify → plan → execute → govern → compose → enqueue → memory
                    ├── classifier.py     tone/intent classification (deterministic fallback)
                    ├── router.py         plan_workflow(): routing.yaml + hysteresis + /mode → WorkflowPlan
                    ├── runner.py         WorkflowRunner: six execution-mode strategies over the agent fleet
                    ├── governance/       risk/confidence/reversibility matrix + interactive approval gate
                    ├── agents/           ten+ workers; composer=True attribute selects the user-facing text;
                    │                     agents/llm_run.py is the one model-call envelope; connector.py is
                    │                     the only door to external MCP servers (via mcp/client.py)
                    ├── memory/           store.py (SQLite, single writer = Memory Agent), vault projection,
                    │                     sqlite-vec embeddings, graph.py, vault_watch.py, write_buffer.py
                    └── delivery/         notification_queue — every outbound message, even sync CLI replies
```

The supporting machinery: `events.py` (the named-event bus that v0.2+ behavior must hook), `commands.py` (the slash-command registry), `invoke.py` (shared agent-invocation core), `llm.py` (LiteLLM wrapper: one retry, token accounting, before/after events), `sandbox.py` (all shell-safety enforcement, in code the LLM cannot rewrite), `daemon.py` (one `DaemonLoop` lifecycle behind the three background daemons), `evolution/` (GP loop, manual entry, fitness, promotions), `authoring/` (skill drafting, quarantine, approval), `profiling.py`, `mcp/` (server.py + client.py, the only modules importing the optional MCP SDK), `web/` (Streamlit, optional extra).

The extension points are deliberate and few: new channels adapt over `channel.run_turn`; new v0.2+ behavior registers handlers on the named events; new tools default to CLI scripts behind the constrained-bash skill (first-class tools require justification — a bar the Connector decision explicitly upheld, ADR-0016); evolvable behavior is addressed by target strings the live-swap read paths consult.

### Stack

Python 3.11+ managed by uv. LiteLLM over OpenRouter for all model calls. Stdlib SQLite plus `sqlite-vec` (lazily loaded, degrades to recency-only recall). YAML config, secrets only in `.env`. Optional extras: `streamlit` (web), the `mcp` SDK (both MCP directions). **Deliberately absent**: LangGraph, Temporal, Ray, Redis, Docker — orchestration is hand-rolled asyncio plus an event bus (ADR-0001). Nothing in the dependency tree is exotic; the load-bearing unusual choice is `sqlite-vec` for embeddings inside the canonical SQLite file.

## Decisions and constraints

Sixteen accepted ADRs in [docs/adr/](docs/adr/). The ones that close off paths, which is what matters for a what-next conversation:

- **0001 Hand-rolled orchestration** — no framework will be adopted; new orchestration complexity must be paid for in plain Python.
- **0002 Single writer + queue** — the Memory Agent is the only writer to durable state; every outbound message flows through `notification_queue`. Any feature that wants to write memory directly or send directly is wrong by construction.
- **0005 Shell safety in `sandbox.py`, not SKILL.md** — enforcement lives in code the LLM cannot rewrite. ADR-0016 reaffirmed this by *rejecting* a CLI bridge for MCP because it would carve a network hole into the sandbox.
- **0006 + 0013 The human approval boundary** — neither the GP loop nor the authoring daemon ever promotes anything to production autonomously; both boot paused. The sandbox allowlist is a human-only change.
- **0008 Live swap via `active_evolutions`** — behavior change from evolution is one DB row consulted by read paths, trivially rollback-able.
- **0014/0015/0016 The post-v0.1 trio** — observability is local-only; new channels are additive adapters over the one seam; external tools live behind exactly one Connector seam, and the first-class tool layer was *deferred, not granted* (adopting it would rewrite the model-call envelope and dissolve the single governance point for "this turn touched the outside world").

Two CLAUDE.md rules constrain everything above and below: new capabilities default to CLI scripts behind constrained-bash, and new v0.2+ behavior ships as event handlers, not pipeline edits.

### The core invariant

**Every consequential action passes through exactly one governed seam, and nothing changes the system itself without explicit human approval.** Turns go through `master.handle` via the channel core (no bypass); durable writes go through the Memory Agent; outbound messages go through the queue; external calls go through the Connector; self-modification (evolved prompts/config, authored skills) goes through quarantine plus a human gate. A new feature that adds a second path around any of these seams violates the project's reason for being.

## Open ground

This is where a strategy conversation has room:

1. **The v0.2 question is "when," not "what."** Telegram's shape is fully pre-decided (transport adapter, `before_send` policy handler, restored auth) and the seams are built. The real decision is whether to do it next, or whether the post-v0.1 pattern — channels, MCP, self-extension — continues. The spec's own discipline argues for Telegram; the commit history shows the gravitational pull is elsewhere.

2. **The LOC budget is spent and the project knows it.** First crossing of the 15k soft target. STATE.md ties the shrink trigger to candidate 19 (a growing `store.py`). Anything proposed next should either be net-negative on `src/` or be the transport v0.2 was always meant to be. "Cut, don't expand the budget" is the project's stated answer.

3. **The openspec ledger needs reconciling.** The fan-out peer-replacement change is implemented and tested in the tree but never archived, which means the repo's own change ledger asserts a recovery gap that no longer exists. Cheap to fix, and worth doing before the stale proposal misleads another review (it misled this one's first draft).

4. **The Connector is deliberately half-armed.** Not auto-routed, no per-call pre-execution gating, no tool-level allowlists (deferred until real Compendium tool names exist). The moment external integrations (Calendar, Gmail) become real, these deferrals come due — and they interact with the approval-over-MCP gap (machine channels cannot approve).

5. **Two self-modification loops exist but boot paused.** Whether the GP loop and authoring daemon are actually *used* — whether promotions and authored skills are happening in practice — is not visible from the tree. If they are not being exercised, that is a product question (the self-improving premise is the project's identity) before it is a technical one. *(Inferred: the tree shows the machinery and its tests, not its usage.)*

6. **The fitness signal for `retry:repair` is structurally weak** and acknowledged as such. Making it behavioral would require fault injection, which is real work for a target of unclear value — a candidate for cutting rather than fixing.

7. **The trust posture is load-bearing and fragile to ambition.** Four channels, no auth, LAN-only by declaration. Telegram (a cloud-relayed channel) is the first feature that genuinely breaks the "everything is local" frame — `allowed_user_ids` is the planned answer, but the secrets/exposure surface grows either way.

*Inference flags: everything in Status/Architecture was verified against the tree, tests, ADRs, and git log today, including the fan-out recovery call sites and their tests. The one remaining inferred claim is the usage of the self-modification loops (not derivable from the repo).*
