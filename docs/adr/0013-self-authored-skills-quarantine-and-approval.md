# 0013 — Self-authored skills: quarantine + human approval boundary

Status: Accepted
Date: 2026-06-09 (post-v0.1 self-extension experiment; authoring Phases 1–5)

## Context

The GP self-improvement loop (ADR-0006/0007) *tunes* what already exists — persona
prompts, routing rules, tool-chains, retry config. It never invents a brand-new
capability. The one remaining item in `docs/ubongo-open-items.md` was the
self-extension gap: letting Ubongo **author new skills**, not just optimize current
ones.

A skill that Ubongo writes for itself is a new trust surface. Two properties make
it sharper than a GP variant:

1. A skill can carry a **constrained-bash command template**, and the sandbox
   allowlist includes `python`, `git`, `sqlite3` — powerful interpreters
   (ADR-0005, and the SECURITY.md "allowlisted interpreters are powerful" note).
   A self-authored command is the system proposing what runs on the machine.
2. A skill's SKILL.md declares its own `risk` / `reversibility`. A model drafting
   a skill could understate them to slip past governance.

So the design question was not "can it write a skill" but "where is the boundary
that keeps a self-authored skill from becoming a live capability without a human
in the loop," reusing the patterns already in place rather than inventing new ones.

## Decision

A new `src/ubongo/authoring/` package mirrors `src/ubongo/evolution/` one-for-one:
draft → quarantine → evaluate → human approval → live, with the GP loop's
discipline (boot paused, throttle by a rolling-hour budget, full lineage + audit,
nothing live without explicit approval).

The boundary has four enforced layers:

- **Quarantine, not the live directory.** Drafts are written to
  `config/skills_candidates/<name>/`, which `skills.py` does **not** scan. A
  quarantined skill is invisible to the classifier and `/skills`. Only
  `promotion.approve` materializes it into the live `config/skills/` and reloads
  the registry. The human gate (`/skill-candidates approve`) is the *only* path
  from quarantine to live.

- **The risk floor is enforced in code, not author-declared.** `validation.validate`
  forces any candidate carrying a command template to `risk >= medium` /
  `reversibility: irreversible`, regardless of what the drafting model wrote, and
  re-runs that check at approve time. A self-authored command skill cannot mark
  itself low-risk.

- **Static command validation reuses the exact run-time sandbox contract.**
  `sandbox.validate_command` (extracted from `run_constrained`) vets a generated
  command template against the same allowlist / metacharacter / path-traversal
  rules that gate real execution — at draft time and again at approve time. A
  command that would be refused at run time is refused before it can register. The
  sandbox allowlist itself stays a **human-only code change** (ADR-0005); the
  authoring layer composes existing allowlisted programs, it never extends the
  allowlist.

- **The autonomous daemon never approves.** `AuthoringLoop` (boot-paused,
  budget-throttled, gap-inferring) only ever produces *quarantined drafts*.
  Approval stays a manual `/skill-candidates approve`. The daemon moving the
  approval boundary would defeat the entire design; it does not.

Reversibility is first-class: `approve` backs up any existing same-named skill to
`config/skills_backups/<name>/<stamp>/` before overwriting, and `rollback` restores
the prior version (or unregisters when there was none). Authoring an updated skill
never destroys what it replaced.

This stays inside the existing ADRs: 0005 (shell safety in code, not SKILL.md),
0006 (self-improvement is approved-not-autonomous). It is the same boundary applied
to a new artifact.

## Consequences

- **One new trust surface, fully gated.** Ubongo can draft skills manually
  (`/author`) and autonomously (the daemon), but a human reviews the exact SKILL.md
  and command shape before anything is discoverable or runnable. At use time, an
  approved skill still runs every turn through the governance matrix and the
  immutable sandbox — defense in depth, nothing new bypassed.

- **Residual risk: the allowlisted interpreters.** Because `python` / `git` are
  allowlisted, an *approved* command skill is as powerful as `/exec` is today
  (SECURITY.md). The boundary that bounds this is the human approving the command
  shape, plus the use-time sandbox — not a narrower allowlist. This is the central
  threat to watch; a fault-free narrowing of the allowlist is a future option, not
  a v-now requirement.

- **Schema + config additive only.** `authored_skills`, `authoring_runs`,
  `authoring_state` ship via `CREATE TABLE IF NOT EXISTS`; the `authoring:` block
  is additive in `settings.yaml`. No migration. Two off-switches keep the test
  suite offline and daemon-free: `UBONGO_DISABLE_AUTHORING_EVAL`,
  `UBONGO_DISABLE_AUTHORING`.

- **Evaluation is the weakest signal.** A brand-new skill has no natural held-out
  set, so `sandbox.evaluate_candidate` scores it over a few generic probes plus the
  candidate's own stated purpose, gated by a command dry-run. It is an estimate to
  inform the reviewer, not an autonomous pass/fail — consistent with keeping the
  human in the loop.

- Verified: full pytest green (≈900) and the Phases 0–21 cumulative smoke plus the
  authoring lifecycle (manual `/author`, the approval gate, the autonomous daemon)
  certified live at each phase merge.

References: `Plans/authoring-self-extension.md`; `src/ubongo/authoring/`
(`candidate.py`, `validation.py`, `quarantine.py`, `sandbox.py`, `fitness.py`,
`promotion.py`, `gaps.py`, `loop.py`); `src/ubongo/sandbox.py` (`validate_command`);
`docs/SECURITY.md` ("Self-authored skills"); ADR-0005, ADR-0006.
