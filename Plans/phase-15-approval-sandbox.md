# Phase 15 — Approval Gates + Sandboxing: Implementation Plan

Date: 2026-05-16
Branch: `phase-15-approval-sandbox` (off `main` at `8a0d7b1`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 15 (lines 1131–1160).

## Context

Phase 14 landed the governance decision matrix: a destructive or high-risk +
irreversible turn now returns `action='require_approval'`, and `master.handle`
replaces the response with the canned `_APPROVAL_REQUIRED_MESSAGE` ("...not
proceeding without explicit approval. (The interactive approval flow lands in
Phase 15.)"). There is no way for the user to actually approve — the gate is a
dead end. And the `governance_decisions.approval_response` column exists but is
always NULL.

Separately, the Execution Agent's sandbox (`sandbox.py`, Phase 11) is solid but
not finished: a 17-command allowlist, metacharacter + path-traversal rejection,
`shell=False`, a 4-key env, a 4-directory PATH, repo-root cwd, 10s timeout,
output caps. The `constrained-bash` SKILL.md itself says "v0.1; sandboxing is
minimal — Phase 15 will harden it."

Phase 15 closes both: an interactive y/n/why approval flow on top of
`require_approval`, and a hardened sandbox (child gets an empty PATH, a
filesystem allowlist, a tightened env). End of Tier 4 (Governance).

## Goal

1. **Approval flow.** A `require_approval` turn becomes a real decision point.
   `governance/approval.py` builds an `ApprovalRequest` (one-line summary + a
   "why" paragraph). The REPL prompts `Approve? (y/n/why)`:
   - `y` → the request is approved; the real answer is delivered.
   - `n` → aborted; the user is back at the prompt.
   - `why` → a one-paragraph risk explanation, then re-prompt.
   The choice is persisted in `governance_decisions.approval_response`.
2. **Sandbox hardening.** The Execution Agent's subprocess gets: an **empty
   PATH** (the parent resolves each allowlisted command to an absolute path; the
   child cannot spawn helpers by name), a **filesystem allowlist** (path
   arguments must resolve inside the repo tree — absolute paths outside are
   refused), and a **tightened env**. The 10s timeout and `shell=False` stay.
3. **`docs/SECURITY.md`** documents the whole contract and its known v0.1 limits.

## Approach — the approval flow

The require_approval turn is treated as a **complete turn that ends in a
question**, exactly like Phase 13's repair-exhausted turn (apology + y/n). It is
not a half-finished turn:

- `master.handle` keeps Phase 14's behavior — a `require_approval` turn delivers
  the canned "needs approval" message and commits it as the assistant turn.
  **Additionally**, when `action == require_approval`, master attaches an
  `approval` payload to the `Response` (`ApprovalRequest` as a dict:
  `decision_id`, `summary`, `why`).
- The REPL gains `_prompt_approval(request)` — mirrors `_prompt_repair_retry`:
  - `why` → print `request.why`, re-prompt (no DB write).
  - `n` → `store.update_governance_decision(decision_id, 'n')`; print an abort
    line; back to the prompt.
  - `y` → `store.update_governance_decision(decision_id, 'y')`; re-issue
    `master.handle(message, …, approved=True)` — the re-run delivers the real
    answer.
- `master.handle` gains an `approved: bool` parameter. When `approved=True` and
  the matrix returns `require_approval`, master overrides the action to `auto`
  with reason `approved_by_user` before applying the gate — so the re-run
  delivers the real answer and its `governance_decisions` row reads
  `action='auto'  reason=approved_by_user`. The trace then reads honestly:
  turn 1 `require_approval (approval_response=y)`, turn 2 `auto
  (approved_by_user)`.
- One-shot is non-interactive: a `require_approval` turn prints the gated
  message and exits `rc=1` (no prompt) — identical to Phase 13's one-shot
  apology path. The user can re-run.

`Response` gets a new `approval: dict | None` field, independent of the
Phase-13 `requires_user_decision`/`repair_summary` (repair) fields — two
separate REPL branches, no overloading.

## Approach — the sandbox

Phase 11 already enforces most of the contract. Phase 15c adds:

- **Empty child PATH.** At module load, `sandbox.py` resolves each allowlisted
  command to an absolute path (`shutil.which` against the current safe
  directories) into a `_PROGRAM_PATHS` map. `run_constrained` dispatches
  `argv[0]` = the resolved absolute path and sets `PATH=""` in the child env.
  Net effect: a child process (`git`, `python`) cannot itself shell out to
  arbitrary tools by bare name. An unresolved command → "(not installed)".
- **Filesystem allowlist.** `_check_paths` gains a positive containment rule:
  any argument that is an absolute path (`/…`) must resolve under `_REPO_ROOT`,
  else `SandboxRefused`. Combined with the existing `..` rejection, every path
  the sandbox touches is provably inside the repo tree. (`cwd` stays repo root —
  `git`, `pytest`, `sqlite3 data/ubongo.db` need it; "project subdir" in the
  spec is satisfied by "contained within the project tree", which the allowlist
  now enforces.)
- **Tightened env.** Drop `HOME` from the child env unless a test shows a tool
  needs it; keep `LC_ALL`/`LANG`. The child gets the minimum.
- **Network** stays governed by the allowlist — no network tool (`curl`,
  `wget`, `ssh`) is allowlisted, so spec scenario 6 (`curl` → refused) passes.
  True OS-level network isolation (seccomp / `sandbox-exec`) is **out of v0.1's
  hand-rolled scope** and is documented as a known limitation in
  `docs/SECURITY.md` (notably: `python`/`python3` are allowlisted and can reach
  the network).

## Non-goals (locked)

- **No OS-level sandboxing** (seccomp, `sandbox-exec`, namespaces). v0.1 stays
  pure-Python subprocess hardening; the network limitation is documented.
- **No broadened allowlist.** The SKILL.md mentions "broaden the allowlist
  behind the approval gate" — deferred; Phase 15 keeps the 17-command allowlist.
- **No approval for `/exec`.** `/exec` stays the debug bypass (no governance).
  Approval gates turns that go through `master.handle`.
- **No chained approvals.** One y/n/why round per turn (the `y` re-run is a
  fresh turn). Matches Phase 13's one-round repair retry.

## Branch + commit strategy

Branch `phase-15-approval-sandbox` off `main` at `8a0d7b1`. Per
`feedback_phase_branch_open_draft_pr`: push + open a **draft PR** right after the
Plan commit, base `main`, title `Phase 15 — Approval Gates + Sandboxing`.

Seven commits (Plan + 15a–15e + STATUS):

- **15a — `governance/approval.py` + Response wiring.** New module:
  `ApprovalRequest` dataclass, `build_request(decision_id, decision, message)`,
  `explain(decision, message)`. `Response` gains `approval: dict | None`.
  `master.handle` attaches the approval payload when `action == require_approval`
  and gains the `approved` parameter (overrides require_approval → auto with
  reason `approved_by_user`). Tests.
- **15b — Approval persistence + REPL/one-shot flow.**
  `store.update_governance_decision(decision_id, approval_response)`.
  `decision_id` added to `Response`. REPL `_prompt_approval` + the
  `if response.approval is not None:` branch (`y` re-issues with
  `approved=True`; `n`/`why` handled). One-shot prints + `rc=1`. Tests.
- **15c — Sandbox hardening** (`sandbox.py`): empty child PATH via
  `_PROGRAM_PATHS` absolute-path resolution, filesystem-allowlist containment in
  `_check_paths`, env tightening.
- **15d — Sandbox tests** (`tests/test_sandbox.py`): child PATH is empty;
  absolute path outside repo refused; in-repo absolute path allowed; resolved
  program runs; `curl` refused (network via allowlist); existing
  metachar/traversal/timeout tests still green.
- **15e — `docs/SECURITY.md`**: the full sandbox contract (allowlist, PATH, env,
  cwd, filesystem allowlist, timeout, output caps), the approval gate, and
  known v0.1 limitations (network not OS-isolated; `python` can reach the net).
- **STATUS + smoke playbook Phase 15 section.**

## Files

New: `src/ubongo/governance/approval.py`, `tests/test_governance_approval.py`,
`tests/test_repl_approval.py`, `docs/SECURITY.md`.
Modified: `src/ubongo/master.py` (`approved` param, approval payload,
require_approval→auto override), `src/ubongo/repl.py` (`_prompt_approval` +
branch), `src/ubongo/oneshot.py` (rc=1 on require_approval),
`src/ubongo/memory/store.py` (`update_governance_decision`),
`src/ubongo/sandbox.py` (empty PATH, filesystem allowlist, env),
`tests/test_sandbox.py`, `tests/test_master.py`, `STATUS.md`,
`tests/manual/smoke_test.md`. Schema: none — `approval_response` already exists.

## Testing plan (spec §Phase 15)

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Approval yes | destructive ask in REPL; `y` | the real answer is delivered; `governance_decisions.approval_response='y'`. |
| 2 | Approval no | `n` | abort line; back at prompt; `approval_response='n'`. |
| 3 | Approval why | `why` | one-paragraph risk explanation; re-prompts y/n. |
| 4 | Sandbox path violation | `/exec cat /etc/passwd` | refused; no read. |
| 5 | Sandbox timeout | `python3 -c "import time; time.sleep(30)"`, 10s cap | killed at 10s, `exit=-1`. |
| 6 | Network blocked | `/exec curl example.com` | refused (`curl` not allowlisted). |

Smoke playbook gets a Phase 15 section (15.1–15.x). Full pytest suite stays
green (491 + ~25 new approval/sandbox tests).

## Open questions (defaults below; push back to change)

1. **The require_approval turn delivers a "needs approval" message and the
   y/n/why is a separate REPL interaction** (mirrors Phase 13's repair y/n);
   `y` re-issues the turn with `approved=True` (one extra LLM call, as Phase 13's
   retry does). OK?
2. **Sandbox cwd stays repo root** (git/pytest/sqlite need it); "project subdir"
   is read as "contained in the project tree", enforced by the new filesystem
   allowlist rather than by relocating cwd. OK?
3. **Network is governed by the allowlist, not OS isolation.** `curl`/`wget`/
   `ssh` aren't allowlisted (scenario 6 passes). seccomp/`sandbox-exec` is out of
   v0.1 scope; the residual gap (`python` can reach the net) is documented in
   `docs/SECURITY.md`, not closed. OK?
4. **`Response.approval` is a new field**, separate from the Phase-13
   `requires_user_decision`/`repair_summary`. OK?
5. **One-shot**: a `require_approval` turn prints the gated message and exits
   `rc=1` (non-interactive — no approve in one-shot). OK?

## Definition of done

- 7 commits on `phase-15-approval-sandbox` (Plan + 15a–15e + STATUS); draft PR
  opened after the Plan commit.
- A destructive REPL turn prompts `Approve? (y/n/why)`; `y` delivers the answer,
  `n` aborts, `why` explains; `governance_decisions.approval_response` persists.
- The sandbox child runs with an empty PATH and a filesystem allowlist;
  out-of-repo absolute paths are refused.
- `docs/SECURITY.md` documents the contract and its limits.
- Testing-plan scenarios 1–6 pass; full pytest suite green.
- `STATUS.md` Phase 15 row → Complete; smoke playbook Phase 15 section appended.
- **End of Tier 4 (Governance).** Branch handed over for merge on your say-so.
