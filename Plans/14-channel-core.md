# Phase 14 — One channel core behind the four fronts

Branch: `improve/14-channel-core`. Lifted from
[Plans/14-19-architecture-deepening-roadmap.md](14-19-architecture-deepening-roadmap.md)
(approved 2026-06-11; "Proceed with the Plan"). Strength: **Strong** — the
evening report's top recommendation. Behavior-neutral: **no VERSION bump**.

## Problem

Every channel re-implements the same turn envelope. `web/turn.py:22-42` and
`mcp/service.py:52-71` are name-substituted twins (`_bootstrapped` flag,
`resolve_startup_profile(None, env)`, the `<channel>_cpu_profiling_on` log);
the profiled-turn block (`profiling.profile_call(master.handle, …)` + report
log + `queue.flush_delivered`) repeats at `oneshot.py:32`, `web/turn.py:58`,
`mcp/service.py:86`, plus a variant in the REPL loop at `repl.py:1435`. The
no-bypass contract (ADR-0002/0003) lives as a convention each channel
re-honors; tonight's worker-thread bug was an envelope bug whose regression
test protects only the MCP copy. v0.2's Telegram transport would be the next
copy.

## Solution

`src/ubongo/channel.py` — the channel core:

- `bootstrap(channel="channel") -> dict` — config + logging once +
  `UBONGO_PROFILE` knob resolution; idempotent; starts NO daemons; logs
  `channel_cpu_profiling_on` with the channel name when the knob arms cpu.
- `cpu_armed() -> bool` — the resolved knob, for fronts that need to ask.
- `run_turn(message, persona, *, auto_mode=False, approved=False,
  pending_skill=None, pending_workflow=None, profile_cpu=None)
  -> tuple[Response, str | None]` — the envelope: optional cProfile wrap
  (`profile_cpu=None` means "use the bootstrap knob"; the REPL passes its own
  session toggle), `master.handle` **resolved at call time** (every existing
  test patch survives — they all mutate the shared `master` module object),
  `queue.flush_delivered`, and a uniform `turn_cpu_profile` log of the
  report's first line. The full report text is returned so the REPL and
  one-shot can also display it.

What stays put: one-shot's printing/exit codes and mem-report flow; web's
rendering and Approve/Deny; MCP's TypedDict shaping, persona validation, and
`anyio.to_thread` hop; the REPL's prompts, retry/approval re-issues, and
per-session toggles.

### Sub-phases

- **14.1** Characterize: every `master.handle` call site + every test patch
  target mapped (done during planning: all patches hit the shared module
  attribute; only `turn._startup_profile` in test_profiling.py is
  seam-specific and moves with the seam).
- **14.2** `channel.py` + port `oneshot.run` (cpu wrap via
  `run_turn(profile_cpu=profile in ("cpu","all"))`; mem flow unchanged).
- **14.3** Port `web/turn.py` and `mcp/service.py` to thin presentation over
  the core; the MCP worker-thread regression test now also proves the seam.
- **14.4** REPL: all three loop call sites (initial, repair-retry, approved
  re-issue) go through `run_turn` — initial with
  `profile_cpu=state.cpu_profile`, the re-issues with `profile_cpu=False`
  (today's behavior, now explicit).
- **14.5** Docs: CONTEXT.md gains a **Channel core** glossary entry; the C4
  containers channels note names the seam. No ADR (ADR-0015's wording already
  describes channels as adapters over the one seam; this makes it literal).

## Behavior to preserve (guarded by tests)

- Every existing channel/profiling test passes unchanged except the one
  `turn._startup_profile` monkeypatch, which moves to the seam.
- Gated turns, repair-exhausted, persona validation: identical per channel.
- `UBONGO_PROFILE` identical everywhere by construction; REPL session toggle
  still wins inside a session.
- New `tests/test_channel.py`: bootstrap idempotence + knob, run_turn happy
  path, flush called, profile_cpu=True writes a `.prof` and returns the
  report, pending/approved passthrough, patch-survival proof.

## Done when

- The envelope exists once; oneshot/web-turn/mcp-service contain no bootstrap
  or flush logic; `pytest -q` green; smoke gate green (it exercises every
  channel); live one-shot + web + MCP turns behave identically.
- Net LOC negative or ~flat (~80-line module replacing ~110 duplicated lines).
