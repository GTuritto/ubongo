# 0024 — Signal: the privacy-respecting messaging channel over a signal-cli sidecar

Status: Accepted
Date: 2026-07-10

> Accepted with the v0.7 line: Phase 00 (the signal-cli sidecar + turn round-trip) and Phase 01
> (the command router + approve-later over Signal, ops, egress, and this acceptance) are built
> and merged. The plan is [Plans/signal-channel.md](../../Plans/signal-channel.md).

## Context

Telegram (ADR-0020) is Ubongo's first cloud-relayed messaging channel and it works — but it is
not the platform Giuseppe prefers. The stated preference is Signal/Matrix over Meta/Telegram, for
privacy reasons. Signal has **no official bot API**; the de-facto integration path is
[signal-cli](https://github.com/AsamK/signal-cli) (AsamK), a Java CLI/daemon wrapping libsignal.

Two facts shape the decision. First, every channel is already a thin adapter over one turn
envelope (`channel.run_turn` → `master.handle` → queue flush), and the trust protocol (v0.5)
already built the remote-channel prerequisites: the resumable approval seam (ADR-0018), the grant
registry (ADR-0019), and the `before_send` policy seam. Telegram proved the pattern. Second,
signal-cli is a **separate process**, not a Python SDK — which is the one thing that makes Signal
structurally different from Telegram.

## Decision

Signal ships as an additive adapter, structured exactly like the Telegram channel (ADR-0020),
with the transport pointed at a locally-run signal-cli daemon:

- **`signal/service.py`** is the channel-free, network-free, unit-testable core — a near-copy of
  `telegram/service.py`: auth (`is_allowed(source_number)`), the
  `/approve|/decline|/pending|/grants` command router (reusing `master.resume_approval` and
  `grant_state` — no re-implemented resume), and `handle_message`, which runs a normal turn
  through `channel.run_turn` (no bypass) and, on a gated turn, surfaces the gated text **and the
  decision_id**.
- **`signal/client.py`** is the only module that talks to signal-cli: **JSON-RPC over a local
  socket** against a running `signal-cli daemon --json-rpc` (receive subscription + `send`), lazy
  import, kept out of core. `ubongo signal` is the entrypoint.
- **The transport is a sidecar, not a library.** Ubongo never imports libsignal; it speaks
  JSON-RPC to a sibling process that owns the Signal protocol and keystore. The `[signal]` pip
  extra is tiny (a socket/JSON client); the real dependency is a **system prerequisite**
  (signal-cli + a JRE), supervised as a service, not `pip install`-ed.
- **A dedicated number**, not a linked device (Giuseppe's call): Ubongo is its own Signal
  identity, registered once via `signal-cli register` → captcha → `verify`, documented as a
  one-time runbook. This keeps his personal account clean and reply attribution unambiguous.
- **Auth returns**, mirroring Telegram: `signal.allowed_numbers` gates who may drive the channel;
  an **empty list denies everyone** (fail-closed).
- **No secret in Ubongo's config.** Unlike Telegram's bot token, the Signal credential is
  signal-cli's own on-disk keystore. `settings.yaml` carries only the bound number, the socket,
  and `allowed_numbers`.
- **Cloud-relay posture, made acceptable by the envelope.** Under ADR-0017, it is the signal-cli
  daemon (running inside the envelope) that reaches Signal's servers; those hosts are the only new
  allowlisted entries. What leaves the machine stays enumerable.

## Consequences

- **Telegram and Signal coexist.** Signal does not supersede ADR-0020; Telegram stays as a peer
  channel. Deprecating Telegram is a later, separate decision.
- **The cloud-relay trust frame is unchanged from Telegram's** — a third party relays messages;
  the controls are `allowed_numbers` (who), the egress envelope (where bytes go), and the
  unchanged governance + grant gates (what an authorized turn may do). Documented, not hidden.
- **Approve-later and grant asks reach Signal with no new resume logic** — the ADR-0018 record and
  `resume_approval` carry it; the channel only renders the prompt and routes the reply.
- **A new ops surface: a supervised sidecar.** signal-cli is a second long-running process with
  its own systemd unit and version to pin. This is the price of Signal having no SDK, and it is
  isolated to `client.py` + the daemon unit.
- **No orchestration change.** REPL/one-shot/web/MCP/Telegram/console and the pipeline are
  untouched; Signal is purely additive.

## Alternatives considered

- **Linked device instead of a dedicated number** — rejected for this line: it makes Ubongo speak
  as Giuseppe from his own account, muddying reply attribution and mixing his message history.
- **Per-message `signal-cli` CLI invocations instead of the JSON-RPC daemon** — rejected: a JVM
  spin-up per received message is the wrong shape for a long-running pump.
- **Matrix** — a valid privacy-respecting alternative and a possible future channel; out of scope
  here (Signal was chosen first).
