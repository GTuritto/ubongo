# The forge — Ubongo as the executable ForgeLoop harness

Status: **DRAFT for revision.** Not sequenced, not approved, version slot open (v0.7 candidate,
after the v0.6 live-console line closes). Part 2 of the ForgeLoop sequence; depends on part 1
([forgeloop-adoption.md](forgeloop-adoption.md)) merging first, because the harness should
enforce the standard the repo has already adopted, not a private variant of it.

Work classification: greenfield (a new capability) inside a brownfield runtime (every seam it
touches already exists and is governed). Rigor mode: Strict, minimum — the forge writes to
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

## Phase sketch (to be split properly when sequenced)

- **Phase 00 — forge session, read-only.** `/mode forge_session <target>`: a registry of target
  repos (path, default branch, standing rigor floor), pre-flight reading through the sandbox,
  work classification + risk + rigor-mode selection, and a drafted phase plan **as response
  text only**. No file writes, no allowlist change. Proves the loop end to end with zero new
  reach.
- **Phase 01 — plan artifacts land in the target repo.** The write seam: the forge writes
  `Plans/`-style docs and ADR drafts into the target working tree, gated as irreversible-ish
  (file creation) through governance. Requires the file-write path decision (below) and any
  allowlist additions — a human-only change by ADR-0005.
- **Phase 02 — implementation slices.** Branch, edit, commit inside the target repo, one
  sub-phase per approval gate, Strict rigor floor. `git` subcommand surface in the sandbox is
  the hard decision here (narrow allowlist: `status/diff/log/add/commit/checkout -b`, no
  `push`, no destructive verbs).
- **Phase 03 — verification and handoff.** Run the target's test command through the sandbox
  (per-target configured, timeout-bounded), compose the execution report and PR body, and stop.
  Pushing and PR creation stay human, or route through the Connector door if ever automated —
  ForgeLoop's own recorded Ubongo lesson: create the PR only after the user explicitly approves
  pushing the branch.

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

## Open decisions (before this becomes a numbered line)

1. **The write mechanism** for target repos: a code-enforced write API (path-rooted to the
   registered target, no traversal, size-capped) versus adding write verbs to the shell
   allowlist. The API is the safer shape; it needs an ADR.
2. **Scope of targets.** Ubongo forging *other* repos only, or allowed to forge *itself*? Self-
   forging collides with the self-modification boundary (GP loop and authoring loop are
   approval-gated); the safe answer is other-repos-only until a dedicated ADR argues otherwise.
3. **Version slot.** v0.7 after the live console, or later — competes with the executor seam
   and any new-channel plan for the next line.
4. **How much of the ForgeLoop reference the forge loads** at runtime (the adapted spine from
   part 1 only, or per-target `AGENTS.md` files it discovers in the target repo).
