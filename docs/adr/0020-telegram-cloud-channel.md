# 0020 — Telegram: the first cloud-relayed channel, additive over the one seam

Status: Accepted
Date: 2026-06-13

## Context

Through Phase 03/05, Ubongo gained persistent, resumable approvals and a grant
registry — but the user could only reach them from a machine on the LAN (REPL,
web, MCP). The trust protocol needs a channel that reaches the user *remotely*:
to carry an approve-later y/n, a grant first-encounter ask, and (Phase 06) a
standing job's "park and raise." The v0.1 plan always named Telegram as that
channel and reserved `allowed_user_ids` for it.

Two facts shape the decision. First, every channel is already a thin adapter
over one turn envelope (`channel.run_turn` → `master.handle` → queue flush), so a
new channel must add no orchestration. Second, Telegram is **cloud-relayed** —
messages transit Telegram's servers — which is a real change from the
local-first posture of every prior channel.

## Decision

Telegram ships as an additive adapter, structured exactly like the MCP channel:

- **`telegram/service.py`** is the channel-free, network-free, unit-testable
  core: auth, the `/approve|/decline|/pending|/grants` command router (reusing
  `master.resume_approval` and `grant_state` — no re-implemented resume), and
  `handle_message`, which runs a normal turn through `channel.run_turn` (no
  bypass) and, on a gated turn, surfaces the gated text **and the decision_id**.
- **`telegram/bot.py`** is the only module that imports httpx and speaks the Bot
  API — a thin long-poll loop (`getUpdates`/`sendMessage`), lazy import, token
  from `TELEGRAM_BOT_TOKEN` in `.env`. `ubongo telegram` is the entrypoint.
- **Auth returns.** `telegram.allowed_user_ids` gates who may drive the bot; an
  **empty list denies everyone** (fail-closed). This is the first channel since
  v0.1 with real auth — earlier channels rely on the LAN posture (ADR-0015).
- **The cloud-relay posture is made acceptable by Phase 01.** Behind the egress
  envelope (ADR-0017), `api.telegram.org` is the only new allowlisted host; what
  leaves the machine stays enumerable. The token never enters config or logs.
- **A `before_send` policy seam** (`service.delivery_allowed`) is wired but
  minimal (a `delivery_paused` switch); the quiet-hours/holds/catch-up engine is
  a later phase.
- The `[telegram]` extra (httpx) is optional and imported only in `bot.py`, so
  the core install and test suite run without it (the streamlit/mcp precedent).

## Consequences

- **The trust frame shifts, knowingly.** A cloud channel means a third party
  relays the messages. The controls are `allowed_user_ids` (who), the egress
  envelope (where bytes go), and the unchanged governance + grant gates (what an
  authorized turn may do). Documented in SECURITY.md, not hidden.
- Approve-later and grant asks now reach the phone with no new resume logic —
  the Phase-03 record and `resume_approval` carry it; the channel only renders
  the prompt and routes the reply.
- A single auth tier: any allowed user can `/approve` any pending decision. With
  one user that is exactly right; a per-decision principal check is a follow-up
  if multiple users are ever allowed.
- Standing jobs (Phase 06) inherit a working remote delivery + approval path;
  this was their prerequisite.
- No orchestration change: REPL/one-shot/web/MCP and the pipeline are untouched;
  Telegram is purely additive.
