# Ubongo Security Model (v0.1)

Ubongo is a personal, local, single-user CLI. It is not a multi-user or
production system. This document describes the two security-relevant surfaces
in v0.1 — the **governance approval gate** and the **execution sandbox** — and
states their known limitations honestly.

## Governance approval gate

Every turn is scored by the governance decision matrix
(`src/ubongo/governance/decision.py`, Phase 14) and resolves to one of
`auto | ask_clarification | require_approval | reject`. The rules and
thresholds live in `config/governance.yaml`; `/policy` prints the live matrix.

A turn scored **`require_approval`** (destructive risk, or high risk that is
irreversible) is not delivered automatically:

- The REPL prints a one-line summary and prompts `Approve? (y/n/why)`.
  - `why` prints a one-paragraph explanation, then re-prompts.
  - `n` aborts the turn; nothing is delivered.
  - `y` re-issues the turn approved; the answer is delivered.
- The choice is persisted in `governance_decisions.approval_response`.
- One-shot mode is non-interactive: a `require_approval` turn prints the gated
  message and exits non-zero. There is no way to approve in one-shot.

In v0.1 the gate governs *answering* a flagged request — Ubongo has no tool
that performs destructive actions on its own. The gate is the seam where, in
later versions, an action would be held.

## Execution sandbox

All shell execution goes through `src/ubongo/sandbox.py::run_constrained`. The
`constrained-bash` skill and the Execution Agent are the only callers; the
`/exec` REPL command is a debug path that calls it directly. The SKILL.md body
is metadata only — the enforcement is in code the LLM cannot rewrite.

### Enforced contract

| Control | Enforcement |
| --- | --- |
| **Command allowlist** | 17 read-mostly commands (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `git`, `python`, `python3`, `pip`, `uv`, `pytest`, `sqlite3`, `echo`, `pwd`, `true`, `false`). `rm`, `mv`, `cp`, `chmod`, `curl`, `wget`, `ssh`, `docker`, `make` are not on it. |
| **No shell** | `shell=False` always. Shell metacharacters (`;`, `\|`, `&`, `` ` ``, `$(`, `>`, `<`) are rejected before parsing. |
| **No path traversal** | `..` and `~` are rejected. Obvious sensitive trees (`/etc`, `/var`) are rejected by fragment. |
| **Filesystem allowlist** | Any absolute-path argument must resolve **inside the repo tree**, else the command is refused. (Phase 15c.) |
| **Empty child PATH** | The parent resolves each allowlisted command to an absolute path at import; the child subprocess runs with `PATH=""`, so it cannot spawn further programs by bare name. (Phase 15c.) |
| **Tight env** | The child gets only `PATH=""`, `HOME=<repo root>`, `LC_ALL=C`, `LANG=C`. Nothing from the parent environment is inherited. |
| **Working directory** | The repo root — contained within the project tree. |
| **Timeout** | 10 seconds by default; a timed-out process is killed and reported with exit code `-1`. |
| **Output caps** | stdout capped at 2 KB, stderr at 1 KB, with a truncation marker. |

### Known limitations (v0.1)

These are deliberate v0.1 scope boundaries, not oversights:

- **No OS-level isolation.** The sandbox is pure-Python subprocess hardening —
  no seccomp, no `sandbox-exec`, no namespaces or chroot. A future hardening
  pass would add this; the contract is kept in one module so it has a single
  place to land.
- **Network is governed by the allowlist, not blocked.** No network tool
  (`curl`, `wget`, `ssh`) is allowlisted, so the obvious cases are refused. But
  `python` / `python3` *are* allowlisted, and an allowlisted interpreter can
  open a socket. v0.1 does not hard-block network egress.
- **The allowlisted interpreters are powerful.** `python`, `python3`, `git`,
  `sqlite3`, `find` can each do a lot within the repo tree (read any tracked
  file, run arbitrary Python). The filesystem allowlist bounds *where*; it does
  not bound *what* an interpreter does inside that boundary.

## Optional web UI (post-v0.1)

The optional Streamlit web channel (`src/ubongo/web/`, `./start-ubongo-web.sh`)
adds a network listener, by design **without authentication or TLS** — it is
intended for a single user on a trusted home LAN. Consequences to be aware of:

- **No login, no transport encryption.** Anyone who can reach the host:port can
  drive the agent. The launcher binds `0.0.0.0` so a tablet on the same network
  can connect; this also means any device on that network can.
- **The agent's governance + sandbox still apply** — the web turn runs through
  the same `master.handle` pipeline as the CLI, so the approval gate (rendered as
  Approve/Deny) and the Execution sandbox above are unchanged. The web UI adds no
  new privileged path; it adds an *unauthenticated* way to reach the existing one.
- **Do not expose it beyond your LAN.** No port-forwarding, no reverse proxy to
  the internet. Authentication/TLS are explicitly out of v0.1 scope. The channel
  is off unless you install (`./install.sh --web`) and start it.

## Reporting

This is a personal project with no external attack surface in the default CLI
configuration (single user, local). The optional web UI is LAN-only by design
(above). There is no disclosure process; security notes belong in the repo's
issue tracker.
