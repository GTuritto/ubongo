# The forge — Ubongo as the executable ForgeLoop harness

Status: **DRAFT — finalized for approval.** Complete plan, awaiting the explicit approval gate
(AGENTS.md non-negotiables); no implementation until approved. **Part 2** of the ForgeLoop
sequence; depends on **part 1** ([forgeloop-adoption.md](forgeloop-adoption.md), PR #57) merging
first, because the harness should enforce the standard the repo has already adopted, not a private
variant of it. **Version slot: v0.8 candidate** — the proposed Signal channel line (`Plans/signal-channel.md`,
PR #58) took the v0.7 slot ahead of it, and this line also does not start until the v0.6 Phase 05
Streamlit clawback has landed (see constraints). Sequenced *last* of the current draft lines.

Work classification: **greenfield** (a new capability) inside a brownfield runtime (every seam it
touches already exists and is governed). Rigor mode: **Strict**, minimum — the forge writes to
repositories and runs their commands, which is trust-spine territory.

## The one-sentence claim

ForgeLoop defines ten steps a harness must run (read repo state, accept a task, pre-flight,
classify risk, select rigor mode, generate plans, stop for approval, run
implement/verify/review loops, collect telemetry, produce handoff artifacts) and defers the
harness itself; Ubongo already owns a governed seam for eight of the ten, so the forge is
mostly *routing* — a new session mode that points the existing machine at a target repository —
not a new machine.

## The mapping (harness step → Ubongo seam)

| ForgeLoop harness step | Ubongo seam that already does it |
| --- | --- |
| 1. Read repository state | Research + Coding agents over the constrained shell (`sandbox.py` allowlist) |
| 2. Accept a task / phase / story | A turn through `master.handle`; entry via a new `/mode forge_session` |
| 3. Pre-flight exploration | `research_session` machinery, scoped to the target repo |
| 4. Classify task risk | The governance decision matrix (risk + reversibility scoring) |
| 5. Select rigor mode | **New**: a forge-planner output field, persisted with the plan |
| 6. Generate or update specs and plans | Coding agent output; the write path is the phase-01 decision |
| 7. Stop for human approval | `pending_approvals` — persisted, resumable, approvable from any channel (a forge gate answered from Telegram works on day one) |
| 8. Implement / verify / review loops | The workflow runner: Coding builds, Evaluator + Critic critique (ForgeLoop ADR 0001's builder/critic split, already live), the repair ladder retries |
| 9. Collect telemetry | `workflow_runs` / `agent_runs` traces — persistence is already non-optional |
| 10. PR notes and handoff artifacts | Composed output through the notification queue; *pushing* is the phase-03 decision |

Steps 5 and 6 are the only genuinely new mechanics. Everything else is scope-widening of an
existing governed seam, which is exactly what the seams are for.

## Phase breakdown

Each phase is its own branch and draft PR, merged by Giuseppe, version `0.8.NN`.

- **Phase 00 — forge session, read-only.** `/mode forge_session <target>`: a registry of target
  repos (path, default branch, standing rigor floor), pre-flight reading through the sandbox,
  work classification + risk + rigor-mode selection, and a drafted phase plan **as response text
  only**. No file writes, no allowlist change. **Exit:** pointing the forge at a real target repo
  produces a classified, rigor-tagged phase plan in the response, fully through `master.handle`,
  with a `workflow_runs` trace and **zero** writes to the target tree.
- **Phase 01 — plan artifacts land in the target repo.** The write seam: the forge writes
  `Plans/`-style docs and ADR drafts into the target working tree, gated as irreversible-ish
  (file creation) through governance. Requires the write-mechanism decision (open decision 1) and
  its ADR. **Exit:** an approved forge turn creates a plan doc + ADR draft inside the target repo
  via the code-enforced write API (path-rooted, no traversal, size-capped); a declined gate writes
  nothing; the write path has its own ADR and unit tests.
- **Phase 02 — implementation slices.** Branch, edit, commit inside the target repo, one
  sub-phase per approval gate, Strict rigor floor. The `git` subcommand surface in the sandbox is
  the hard call: a narrow allowlist (`status/diff/log/add/commit/checkout -b`, **no** `push`, no
  destructive verbs), enumerated for human-only approval (ADR-0005). **Exit:** an approved slice
  produces a committed change on a fresh branch in the target repo; `push` is impossible from the
  sandbox; every allowlist addition is a reviewed, human-only diff.
- **Phase 03 — verification and handoff.** Run the target's test command through the sandbox
  (per-target configured, timeout-bounded), compose the execution report and PR body, and stop.
  Pushing and PR creation stay human, or route through the Connector door if ever automated —
  ForgeLoop's own recorded Ubongo lesson: create the PR only after the user explicitly approves
  pushing the branch. **Exit:** a forged slice ends with a test run, an execution report, and a
  PR-body draft in the response; nothing is pushed and no PR is opened by the forge.

## QA test plan

Work classification: greenfield. Rigor mode: Strict.

### Acceptance criteria (exit = all checked, per phase where noted)

- [ ] **AC-1 No-bypass.** Every forge turn goes through `channel.run_turn` → `master.handle`; it
      is classified, governed, and persisted (a `workflow_runs` row) exactly like any turn. The
      forge adds no orchestration path and no new door.
- [ ] **AC-2 Read-only is truly read-only (P00).** In `forge_session` before Phase 01, no bytes
      are written to the target tree under any prompt; the pre-flight uses only sandbox reads;
      the produced plan carries a work classification and a rigor mode.
- [ ] **AC-3 Writes are code-enforced, not shell (P01).** Target-repo writes go through the
      constrained write API (path-rooted to the registered target, no `..` traversal, size-capped),
      not through shell redirection; a write outside the registered target root is refused; the
      mechanism has its own ADR.
- [ ] **AC-4 Every write/command is governed (P01/P02).** File creation and each git subcommand
      are scored through the governance matrix and gated; a declined gate leaves the target tree
      byte-unchanged; approvals resolve the one `pending_approvals` record (answerable from any
      channel).
- [ ] **AC-5 Sandbox allowlist stays human-only (ADR-0005).** The forge never edits the
      allowlist; each phase's new commands are enumerated in the plan for explicit approval; `push`
      and destructive git verbs are absent from the allowlist.
- [ ] **AC-6 No external reach (ADR-0016).** Phases 00–03 make no network call; if PR automation
      is ever wanted it is a Connector MCP server, not a `gh`/`curl` binary in the sandbox.
- [ ] **AC-7 Self-forging boundary (open decision 2).** Until a dedicated ADR argues otherwise,
      the target registry refuses Ubongo's own repo, so the forge cannot collide with the
      GP/authoring self-modification boundary.
- [ ] **AC-8 LOC discipline.** The subsystem lands under ~800 LOC by maximal reuse (no new
      orchestration, no new agent classes if the existing ten suffice); if it can't, the scope is
      re-cut before merge, not the budget raised.
- [ ] **AC-9 Additive + green.** REPL/one-shot/web/MCP/Telegram/console and all existing
      orchestration are byte-unchanged; the full pytest suite is green with new
      `tests/test_forge_session.py` (+ write-API + target-registry tests); each phase ships its
      ADR(s) and CHANGELOG `## v0.8.NN`.

### Regression plan (layered)

1. **Unit.** The target registry (register/resolve, rigor floor, self-repo refusal); the
   forge planner (classification + risk + rigor-mode fields on a fixture repo); the write API
   (path-root enforcement, traversal refusal, size cap) with a temp target tree; the git-subcommand
   guard (allowed verbs pass, `push`/destructive verbs refused).
2. **Governance integration.** A forge write and a forge commit each produce a scored decision and
   a `pending_approvals` record; decline leaves the tree unchanged; approve proceeds — asserted
   against the trace, not the response alone.
3. **Deterministic surface.** `/mode forge_session` help/usage; an unregistered target → friendly
   refusal, no turn side effects; `forge_session` with no write phase enabled never mutates a tree.
4. **Live (manual / Pi or dev box).** Point the forge at a scratch target repo: a read-only plan
   turn; an approved plan-artifact write; an approved one-file slice + commit on a new branch; a
   test run + execution-report + PR-body draft with nothing pushed.
5. **Cumulative playbook** sections stay the contract; a new section is this line's acceptance
   surface, appended to `tests/manual/smoke_test.md` phase by phase.

### Smoke section (new section, grows phase by phase)

P00: `/mode forge_session <scratch>` → classified, rigor-tagged plan in the response, zero target
writes · P01: approved write creates a plan doc + ADR draft via the write API; a traversal path is
refused; decline writes nothing · P02: approved slice commits on a fresh branch; `push` refused
from the sandbox · P03: target test command runs, execution report + PR-body draft composed,
nothing pushed · self-repo target refused · full pytest.

## Constraints this plan must survive

- **LOC budget.** ~17,400 LOC against a ~15,000 soft target, and the rule is cut-don't-expand.
  The forge is a new subsystem and must carry its justification: a target under ~800 LOC by
  maximal reuse (no new orchestration, no new agent classes if the existing ten suffice), and
  it does not start until the v0.6 Phase 05 Streamlit clawback has landed.
- **No seam bypass.** Turns through `master.handle`, writes to Ubongo's own memory through the
  Memory Agent, outbound through the queue, shell through `sandbox.py`, approvals through
  `pending_approvals`. The forge adds zero new doors.
- **Sandbox allowlist is a human-only change** (ADR-0005). Each phase enumerates its exact new
  commands in the plan for explicit approval; the forge never edits the allowlist.
- **External reach through the Connector only** (ADR-0016). Nothing in phases 00–03 needs the
  network; if PR automation is ever wanted, it is a Connector MCP server, not a `gh` binary in
  the sandbox.
- **Target-repo file writes are new ground.** Ubongo's one-writer rule covers *its own* durable
  memory; a target working tree is neither Ubongo memory nor Ubongo's repo. The write mechanism
  (a constrained write API in code next to `sandbox.py`, not free shell) needs its own ADR and
  is the single biggest design decision in the line.

## Relationship to the other draft plan

[pluggable-execution-backend.md](pluggable-execution-backend.md) splits *where* a validated
command runs (local / SSH / container) from *whether* it is allowed. The two compose: a forge
working a repo that lives on another box would run its reads, tests, and commits through a
remote executor. Neither depends on the other; if both are sequenced, the executor seam lands
first (it is a refactor of the chokepoint the forge will lean on hardest).

## Open decisions (need Giuseppe's call at approval)

1. **The write mechanism** for target repos: a code-enforced write API (path-rooted to the
   registered target, no traversal, size-capped) versus adding write verbs to the shell
   allowlist. The API is the safer shape; it needs an ADR. Recommendation: the write API.
2. **Scope of targets.** Ubongo forging *other* repos only, or allowed to forge *itself*? Self-
   forging collides with the self-modification boundary (GP loop and authoring loop are
   approval-gated); the safe answer is other-repos-only until a dedicated ADR argues otherwise.
   Recommendation: other-repos-only for this line.
3. **Version slot.** v0.8 after Signal (v0.7) and the v0.6 console close — or later, if the
   executor seam ([pluggable-execution-backend.md](pluggable-execution-backend.md)) is sequenced
   first (it is the chokepoint the forge leans on). Recommendation: sequence the executor seam
   first *if* both are wanted, else v0.8.
4. **How much of the ForgeLoop reference the forge loads** at runtime: the adapted spine from
   part 1 only, or per-target `AGENTS.md`/`FORGELOOP_CORE.md` files it discovers in the target
   repo. Recommendation: discover the target's own spine when present, fall back to part 1's.
