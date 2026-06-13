# 0019 — The grant registry: persistent capability grants, checked at decision time

Status: Accepted
Date: 2026-06-13

## Context

Through Phase 03, every governed action that needed a human yes asked *every
time*. For the Connector (the only external-call surface, ADR-0016), that means
a destructive or high-risk server re-prompts on every turn, even after you have
approved that capability a hundred times. The v0.5 trust-protocol plan set out to
replace that fatigue with persistent consent: ask once, remember, revoke when you
want to. ADR-0016 deferred per-call gating "until real tool names exist"; this is
the form of it we can deliver now.

A real constraint shapes the granularity: the Connector plans its tool calls with
its model at *execute* time, so at governance-*decision* time we do not yet know
which tools it will call — only which servers are enabled (each with a declared
`risk`). So grants are **server-granular**, not per-tool.

## Decision

A **grant** is persistent human consent for a capability class.

- **`grants` table** (additive, `CREATE IF NOT EXISTS`): `capability_class`
  (`connector:<server>`), `consequence_class`, `scope` (`*` or an agent name),
  `purpose`, `status` (`active|revoked`), timestamps. CRUD in
  `memory/grant_state.py`, written by the orchestrator — the same governance
  carve-out as `governance_decisions`, not the Memory Agent.
- **Checked at decision time.** A new rule in `governance/decision.py` (after the
  destructive and irreversible-high *safety* rules, so safety always wins): for a
  connector turn, if any capability class it touches has no active grant →
  `require_approval` (`grant_first_encounter:<class>`); if all are granted → fall
  through to `auto`. Non-connector turns never touch the registry. Fail-closed: an
  unreadable registry counts the class as ungranted (ask).
- **Approval is the grant.** When an approved connector turn proceeds (the
  Phase-03 `approved=True` re-issue) and lacks a grant, the orchestrator writes an
  active grant for its class — so the *next* turn auto-proceeds. Declining writes
  nothing.
- **Revocation** flips `status=revoked`; the next turn re-arms the ask. DB-backed,
  so it survives restart. Managed via `/grants` + `/grants revoke <id>` and the
  CLI `ubongo grants [revoke <id>]`.
- **Chief-of-Staff-as-config (minimal).** The `scope` column carries the agent a
  grant applies to; the enforced invariant is "a capability requires a grant — an
  agent cannot exercise one the registry lacks." Full chartered-role routing
  (roles = registry rows + routing config) is left as configuration to add when a
  second grant-holding agent exists; no role *system* is built here.

This extends ADR-0004 (the decision matrix gains a rule) and ADR-0016 (the
Connector seam gains a persistent gate); it moves connector approval from
ask-every-time toward grant-checked-once, without weakening the safety rules.

## Consequences

- **Per-tool-name allowlists stay deferred** (ADR-0016): a grant covers a whole
  server, so a `connector:compendium` grant authorizes any tool that server
  offers. When a real integration gives concrete, stable tool names, a finer
  `connector:<server>/<tool>` class is an additive follow-up.
- A grant is a standing authorization to touch the outside world; the egress
  envelope (ADR-0017) and the per-server `risk`/`enabled` flags remain the
  coarser controls beneath it.
- **Paired cut (v0.5 plan Amendment 2):** the `retry:repair` evolvable target and
  its structural-proxy fitness are removed. Its fitness never had a behavioral
  basis — offline samples can't induce real failures (ADR-0007) — so it was the
  weakest signal in the GP layer; deleting it is the budget offset for the
  registry, and Repair config stays human-edited in `settings.yaml`.
- Grants are the substrate Phase 06 (standing jobs) builds definition-time grant
  bundles on, and the trust primitive a remote channel's approve-later resolves
  into.
