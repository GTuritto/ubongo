# v0.7 — The Signal channel (the privacy-respecting messaging channel)

Status: **DRAFT — roadmap prep, not approved.** Docs-only until this plan is approved; no
implementation until the gate is passed (AGENTS.md non-negotiables). Proposed as a new minor line
**v0.7**, sequenced *ahead of the remaining v0.6 console phases* (Giuseppe's call: prioritize
Signal over finishing v0.6/01-05). Branches follow the house convention `v0.7/NN-<name>` →
`0.7.NN` (CI derives the version from the branch name; do not hand-edit VERSION).

Work classification: **greenfield** (a new capability — a sixth-plus channel). Rigor mode:
**Strict** (a new inbound door to `master.handle` plus a new outbound egress path; it touches the
trust spine's channel/auth/egress surface, so it gets `Strict` minimum per AGENTS.md).

## The one-sentence claim

Signal is the privacy-respecting messaging channel Giuseppe actually prefers (Signal/Matrix over
Meta/Telegram); it ships as an additive adapter over the one turn seam — the exact Telegram
pattern (ADR-0020) — with one real difference: the transport is a locally-run **signal-cli
sidecar** spoken to over JSON-RPC, not a pip SDK, which makes the relay itself local even though
messages still transit Signal's servers.

## Why now, and where it sits

The trust protocol (v0.5) already built everything a remote messaging channel needs: the
resumable approval seam (ADR-0018), the grant registry (ADR-0019), and the `before_send` policy
seam. Telegram (ADR-0020) proved the pattern end to end. Signal reuses all of it; it adds no
orchestration. Giuseppe has chosen to build it before the v0.6 console's roster/activity phases
(01-05), so the sequencing is:

1. Merge PR #57 (ForgeLoop docs) and PR #56 (v0.6/00 streaming seam) into `main`.
2. Build this line (`v0.7/00`, `v0.7/01`) off the updated `main`.
3. Resume v0.6 (`v0.6/01-agent-roster` onward).

The one consequence to confirm at approval: release tags won't be strictly monotonic in
wall-clock time (a `0.7.x` tag lands before `0.6.1`). See open decisions.

## The shape

A channel is a presentation layer over the one turn envelope (`channel.run_turn` →
`master.handle` → queue flush). Signal is an additive adapter over it, mirroring Telegram's
two-module split, with the transport module pointed at a signal-cli daemon instead of the
Telegram Bot API:

- **`signal/service.py`** — the channel-free, **network-free, unit-testable** core, a near-copy
  of `telegram/service.py`. Auth (`is_allowed(source_number)` against `signal.allowed_numbers`)
  and `handle_message(text, source_number) -> reply` that either (a) refuses an unauthorized
  sender, (b) handles `/approve|/decline|/pending|/grants` via the existing seams
  (`master.resume_approval`, `grant_state`), or (c) runs `channel.run_turn(text, persona,
  auto_mode=True)` and, on a gated turn, surfaces the gated text **and the decision_id**. No
  bypass: every turn goes through `master.handle`.
- **`signal/client.py`** — the **only** module that talks to signal-cli: a small **JSON-RPC over
  a local socket** client against a running `signal-cli daemon --json-rpc`, with a receive
  subscription (incoming messages) and a `send`. Lazy import, kept out of core. Config (the
  bound number, the socket/host) from `settings.yaml`; no secret here — Signal's credential is
  signal-cli's own keystore on disk, not a token Ubongo holds.
- **`ubongo signal`** — the entrypoint that runs the receive→handle→reply pump.

### The one real design difference from Telegram: the sidecar

Telegram was a pure pip extra (`httpx`). Signal is not: signal-cli is an external Java daemon.
That changes three things, and this is the substance of the line.

- **Transport is a sibling process, not a library.** Ubongo speaks JSON-RPC to a locally-running
  `signal-cli daemon`. The daemon owns the Signal protocol, the keystore, and the socket. Ubongo
  never imports libsignal. The `[signal]` pip extra is therefore tiny (a JSON-RPC client, likely
  stdlib sockets + json); the real dependency is a **system prerequisite** (signal-cli + a JRE),
  installed and supervised like a service, not `pip install`-ed.
- **Registration is an operational runbook, not code.** A dedicated number is registered once via
  `signal-cli -a +NUMBER register` → solve the Signal captcha → `verify CODE` (needs a number
  that can receive an SMS/voice code). This is a documented one-time setup step, not part of the
  turn path.
- **Egress has two actors.** Under the envelope (ADR-0017), it's the signal-cli daemon — not
  Ubongo — that reaches Signal's servers, so Signal's hosts (`chat.signal.org`, the CDN hosts)
  go in `deploy/envelope/egress.hosts`, and the daemon runs inside the same envelope. Cloud-relay
  posture is identical to Telegram's (ADR-0020) and gets the same explicit note.

## Phase breakdown (proposed)

Each phase is its own branch and draft PR, merged by Giuseppe, version `0.7.NN`.

- **Phase 00 — The signal-cli sidecar + turn round-trip.** Stand up the daemon (JSON-RPC),
  document the dedicated-number registration runbook, `signal/client.py` (receive + send over the
  socket), `signal/service.py` (auth + `handle_message` → `channel.run_turn`), the `ubongo
  signal` entrypoint. **Exit:** an authorized Signal message from Giuseppe's phone round-trips a
  full turn through `master.handle` and a plain reply comes back; an unlisted sender is refused
  and no turn runs.
- **Phase 01 — The trust surface + ops.** The `/approve|/decline|/pending|/grants` command
  router; **approve-later over Signal** (the headline — a gated turn surfaces the decision_id,
  `/approve <id>` resolves it via `master.resume_approval`); the `before_send` policy seam reused;
  the `[signal]` optional extra; ctl + systemd units for **both** the channel and the signal-cli
  daemon + start scripts + installer; Signal's hosts added to the egress allowlist;
  **ADR-0024** accepted; the smoke section; the "channel preference" docs retargeted so Signal is
  recorded as shipped alongside Telegram. **Exit:** approve-later works from the phone over Signal;
  the channel is supervised and shippable.

Not in scope: linked-device mode (dedicated number was chosen); Matrix; retiring Telegram (it
works and stays); any orchestration change; any change to the v0.6 line.

## QA test plan

Work classification: greenfield. Rigor mode: Strict.

### Acceptance criteria (exit = all checked, per phase where noted)

- [ ] **AC-1 No-bypass.** Every authorized Signal turn goes through `channel.run_turn` →
      `master.handle`; it is classified, governed, and persisted exactly like a typed turn (a
      `workflow_runs` row, a queue row). The receive loop never drives orchestration.
- [ ] **AC-2 Auth fail-closed (P00).** An empty `signal.allowed_numbers` denies everyone; an
      unlisted number gets a refusal and **no turn runs** (no `workflow_runs` row); a listed
      number is served.
- [ ] **AC-3 Approve-later over Signal (P01, the point).** A gated turn replies with the gated
      message **and the decision_id**; `/approve <id>` resolves it via `master.resume_approval`
      and delivers the real answer; `/decline <id>` declines; `/pending` and `/grants` list.
      Reuses ADR-0018/0019 — no re-implemented resume.
- [ ] **AC-4 Transport isolation.** signal-cli/the JSON-RPC client is imported only in
      `client.py`; `service.py` and the whole core import without the `[signal]` extra; the suite
      is green with the extra absent (the client's tests mock the socket).
- [ ] **AC-5 before_send seam (P01).** A registered `before_send` handler can suppress/hold a
      Signal delivery without touching the core; default is allow (behaviour-neutral).
- [ ] **AC-6 No secret in config.** Ubongo holds no Signal token; the credential is signal-cli's
      on-disk keystore. `settings.yaml` carries only the bound number, the socket, and
      `allowed_numbers`; nothing secret is logged.
- [ ] **AC-7 Egress enumerable.** Signal's hosts are the only new allowlisted entries in
      `deploy/envelope/egress.hosts`; the signal-cli daemon runs inside the envelope; what leaves
      the machine stays enumerable (ADR-0017).
- [ ] **AC-8 Additive.** REPL / one-shot / web / MCP / Telegram / console and all orchestration
      are byte-unchanged; no new *core* dependency (the extra is optional; signal-cli is a
      documented system prerequisite, not a Python import in core).
- [ ] **AC-9 Ops surfaces (P01).** `ubongo signal` starts the pump; missing extra/daemon →
      friendly hint, rc 1, no traceback; ctl `start|stop|status signal` and the daemon unit work;
      systemd units + start scripts present.
- [ ] **AC-10 Full suite green** with new `tests/test_signal_service.py`; ADR-0024 + CHANGELOG
      `## v0.7.x` shipped (version CI-derived).

### Regression plan (layered), mirrors the Telegram plan

1. **Unit (surface is `service.py`).** `is_allowed` (empty/unlisted/listed); the command router
   (`/approve`, `/decline`, `/pending`, `/grants`, unknown); `handle_message` for a normal turn
   (mock `channel.run_turn`) and a gated turn (asserts the decision_id is surfaced); the refusal
   path writes no turn.
2. **Client (socket mocked).** `client.py`'s receive→reply pump with the JSON-RPC socket patched:
   a parsed incoming payload drives `service.handle_message` and a `send` call; the missing
   extra/daemon produces the friendly hint.
3. **Deterministic surface.** `ubongo signal` without the extra/daemon → hint + rc 1; help/usage.
4. **Live (manual / needs the registered number + daemon).** Real Signal: an authorized chat
   round-trips a turn; an unlisted number is refused; a destructive prompt gates and `/approve
   <id>` delivers from the phone. (Pi-only, like the Telegram/MCP/envelope live checks.)
5. **Cumulative playbook** sections stay the contract; a new section is this line's acceptance
   surface.

### Smoke playbook section (new section, lands with Phase 01)

`ubongo signal` without the extra → friendly hint, rc 1 · unit: unlisted number refused, no turn ·
unit: authorized normal turn round-trips through `master.handle` · unit: gated turn surfaces the
decision_id; `/approve <id>` delivers · `/pending` + `/grants` over the Signal command router ·
ctl + daemon unit start/status/stop (Pi) · live: authorized chat round-trip + an unlisted refusal
(Pi, needs the registered number) · full pytest.

## Risks and coordination

| Risk | Impact | Mitigation |
| --- | --- | --- |
| signal-cli is a JVM sidecar, not a pip dep | new ops surface; a second process to supervise | isolate all of it in `client.py` + a systemd unit for the daemon; AC-4/AC-9; core imports clean without it |
| Dedicated-number registration needs a captcha + a number that receives a code | one-time setup friction | a documented runbook in Phase 00; it is setup, not the turn path |
| Auth defaults open | anyone messaging drives the agent | empty `allowed_numbers` = deny all; AC-2 locks fail-closed with a test |
| Cloud-relay weakens local-first | trust-frame shifts | identical to Telegram; ADR-0024 names it; the egress envelope + `allowed_numbers` are the controls |
| signal-cli protocol/version drift | daemon breaks silently | pin the signal-cli version in the runbook; the pump logs and continues on a transient RPC error (the daemon crash-swallow pattern) |
| Non-monotonic release tags (0.7.x before 0.6.1) | version history reads oddly | an open decision below; either accept it or renumber |
| LOC budget already ~16% over | a new channel widens the gap | it's a *transport over an existing seam* (cheap by the CLAUDE.md rule), near-copying Telegram; no new subsystem |

## Open decisions (need Giuseppe's call at approval)

1. **Line numbering / sequencing.** New line **v0.7** built before v0.6/01-05 (the chosen
   "prioritize sooner"), accepting non-monotonic release tags — or continue the current line as
   `v0.6/06+` despite the theme mismatch, or finish v0.6 first and take v0.7 after. Recommendation:
   v0.7 built next, accept non-monotonic tags (the branch-derived version doesn't require
   wall-clock monotonicity).
2. **signal-cli integration mode.** JSON-RPC daemon over a local socket (recommended — a
   long-running pump, no JVM spin-up per message) vs per-message `signal-cli` CLI invocations
   (simpler, but spawns a JVM each receive — wrong for a bot).
3. **Phase count.** Two phases as above (transport, then trust-surface+ops), or fold into one
   like Telegram did. Recommendation: two — the sidecar + registration + JSON-RPC client is more
   surface than Telegram's httpx extra, and one-session-per-phase is the house rule.
4. **Telegram's fate.** Keep Telegram as a peer channel (recommended — it works, it's shipped),
   or deprecate it now that the preferred platform exists. Recommendation: keep; revisit later.
