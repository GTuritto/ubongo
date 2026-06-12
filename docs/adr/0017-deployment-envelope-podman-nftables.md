# 0017 — The outer envelope: rootless Podman + UID-keyed nftables egress; deployment, not architecture

Status: Accepted
Date: 2026-06-12

## Context

Everything Ubongo can do to the outside world funnels through two seams the
architecture already governs: the model-call envelope (every LLM and embedding
call → LiteLLM → OpenRouter) and the Connector agent (ADR-0016). But the
*enforcement* of "what leaves the machine" above the network layer is partial
by admission: SECURITY.md flags that the Connector's tool arguments leave the
machine controlled only by per-server `risk`/`enabled` flags, and the shell
sandbox blocks network *programs*, not the network. The v0.5 plan (Decision 3)
calls for an outer envelope so egress is enumerable and enforced below the
model's discretion.

A constraint hangs over the shape: the project's stated absence of Docker.
That exclusion (CLAUDE.md, ADR-0001's spirit) is about the *dependency tree
and architecture* — no orchestration frameworks, no services the code depends
on. It was never a promise about how the host wraps the process.

## Decision

Ship the envelope as **deployment infrastructure, zero `src/` LOC**, under
`deploy/envelope/`:

- **A dedicated `ubongo` user** owns the install; its UID is the firewall key.
- **Rootless Podman Quadlets** (`ubongo-web.container`, `ubongo-mcp.container`,
  one `Containerfile`) replace the bare systemd units on enveloped hosts.
  `.env` is bind-mounted **read-only** (secrets never enter the image);
  `data/` and `vault/` are the only writable mounts; `UserNS=keep-id` keeps
  file ownership and the UID-keyed firewall coherent. Rootless networking
  (pasta/slirp4netns) originates container traffic from the user's own
  processes, so the skuid match covers container egress without root.
- **An nftables output chain keyed on `meta skuid ubongo`**: allow established
  flows, loopback, DNS, and TCP/443 to addresses in two named sets; reject
  (and count + log) everything else. The sets are populated from
  `/etc/ubongo/egress.hosts` — the one-file, human-edited, enumerable answer
  to "what can this machine talk to" — by `refresh-egress.sh` on a 15-minute
  timer. Resolution failure keeps the previous addresses: the design fails
  closed against new hosts, never open.
- **Enabling an MCP server is a two-edit act**: the `settings.yaml` block and
  the `egress.hosts` line, deliberately in the same change. Forgetting the
  firewall edit fails closed with a visible connection error.

The code is unchanged: no governance rule, no sandbox edit, no new module.
The architecture rules this extends, not amends: 0005 (enforcement lives where
the LLM cannot rewrite it — here, below the OS process), 0015 (LAN posture),
0016 (the Connector stays the only outbound seam; the envelope bounds it).

## Consequences

- **Platform asymmetry, named** (plan Amendment 1): Quadlets and nftables are
  Linux-only. The macOS dev machine is NOT enveloped and keeps the LAN-trust
  posture. The egress guarantee holds on one host class; no macOS equivalent
  (pf anchors) is planned.
- The exit criterion is verified **by attempt** on the device
  (`deploy/envelope/INSTALL.md` §4, playbook section E): allowlisted turns
  succeed; a non-allowlisted fetch and a Connector call to an unlisted host
  die at the network layer with the reject counter advancing.
- Accepted residuals: DNS for the ubongo UID is open (a low-bandwidth tunnel
  in theory; pin to the local resolver for a tighter posture), and inbound
  no-auth LAN access to the published ports is unchanged (0015). Both are
  documented in the conf file where an operator will actually see them.
- The **terrarium** (the v0.5 plan's growth experiment) falls out as a second
  user with a looser table and inert keys; the human approval boundary (0006,
  0013) still holds inside it.
- Future MCP integrations inherit a forcing function: a server that is not
  consciously allowlisted does not get network, whatever the model plans —
  which is exactly the posture the grant registry (Phase 05) will formalize
  one layer up.
