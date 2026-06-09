# Plan: Self-Authored Skills (the self-extension experiment)

## Context

This is the one remaining open item in [docs/ubongo-open-items.md](docs/ubongo-open-items.md):
new-capability *authoring*. The GP loop already tunes what exists (prompts, routing,
tool-chains, retry config); it never invents a new capability. This feature lets Ubongo
draft brand-new **skills** and, behind a human gate, register them so they become live
capabilities. It is a deliberate, narrow experiment, distinct from the GP loop, and it
inherits the project's existing safety patterns rather than inventing new ones.

Decisions taken with the user (2026-06-09):

- **Trigger: both.** A manual `/author <description>` core, plus an autonomous background
  daemon that drafts candidates on its own. The daemon is built on top of the same core.
- **Capability scope: prompt-shaped skills and constrained-bash skills.** An authored skill
  is a `config/skills/<name>/` folder (SKILL.md + prompt templates), optionally carrying a
  constrained-bash command template built **only** from the already-allowlisted programs in
  `sandbox.py`. The sandbox allowlist itself stays a human-only code change.
- **Boundary: quarantine then approve.** Drafts land in a quarantine area invisible to the
  runtime. Nothing becomes discoverable until the user approves it through a
  `/skill-candidates` gate that mirrors `/improvements`. Approval registers it; rollback
  unregisters it.

Outcome: Ubongo can propose new skills you did not specify, you review each one with its
full SKILL.md and command shape, and only your approval makes it real.

## Design at a glance

A new `src/ubongo/authoring/` package mirrors `src/ubongo/evolution/` one-for-one. The
GP loop is the proven template (boot paused, throttle by rolling-hour budget, generate,
evaluate side-effect-free, persist lineage + audit, require explicit human approval before
anything goes live). We reuse its mechanisms directly where we can.

Lifecycle: `draft -> quarantine -> evaluate -> /skill-candidates approve -> back up any existing
version -> materialize into config/skills/ + reload -> live`. Reject archives the draft; rollback
restores the backed-up previous version (or unregisters if there was none).

**Versioned backups (user requirement).** Registering a skill never destroys a prior version.
Before approve writes into `config/skills/<name>/`, if a folder is already there (a previously
approved authored skill, or a hand-written one) it is copied to
`config/skills_backups/<name>/<timestamp>/` first, and the backup path is recorded. Rollback then
restores the most recent backup rather than just deleting, so authoring an updated skill is fully
reversible.

The safety story (stated up front because it is the whole point):

1. **Static validation at draft time.** Every candidate is validated against the exact
   `skills._parse_skill` schema (reusing its vocab constants) and, if it carries a command
   template, against the `sandbox.py` static checks (allowlisted program only, no shell
   metacharacters, no path traversal). Invalid candidates never persist.
2. **Risk floor enforced, not author-declared.** Any candidate that emits a constrained-bash
   command is forced to `risk: medium` / `reversibility: irreversible` regardless of what the
   drafting LLM wrote, so a self-authored skill cannot mark itself low-risk to dodge the gate.
3. **Human gate before discoverability.** Quarantine lives in `config/skills_candidates/`,
   which `skills.py` does not scan. Only `/skill-candidates approve` materializes the folder
   into `config/skills/` and calls `skills.reload()`.
4. **Use-time enforcement unchanged.** Once live, an authored skill runs every turn through the
   existing governance matrix + the immutable `sandbox.py` at execution time. The known fact
   that `python`/`git` are allowlisted (so a command template is powerful) is bounded by the
   same sandbox that bounds `/exec` today, plus a human reviewed the exact command shape before
   it could ever run.

## Reuse (do not reinvent)

- `evolution/sandbox.py::CallBudget` and the rolling-hour budget pattern (`store.calls_in_last_hour`-style).
- `evolution/loop.py::EvolutionLoop` daemon shape (boot-paused, `_should_cycle` pure gate, daemon thread, start/stop on the REPL try/finally).
- `evolution/fitness.py::rank_cohort` weighted-sum + deterministic tiebreak, for candidate quality scoring.
- `evolution/promotion.py` `_bust_caches()` reload pattern; `approve/reject/rollback` shape.
- `skills.py` `_parse_skill` + `RISK_VOCAB` / `REVERSIBILITY_VOCAB` / `PERSONA_VOCAB` constants and the prompt-path traversal guard.
- `sandbox.py` `ALLOWED_COMMANDS` + the metacharacter / path-traversal checks (expose a public static validator if one is not already callable without executing).
- `memory/vault.py::append_audit_entry` (+ `_AUDIT_CATEGORIES`) and the `/audit` command.
- `commands.py` registry + `repl.py` handler/`ReplState` pattern; `repl._diff_preview` / `_render_improvements_list` rendering.
- `config.py` cached-loader pattern (`load_evolution`); `store.py` `CREATE TABLE IF NOT EXISTS` + accessor-pair convention.
- `llm.complete` called directly (as `generator.py` does), drafting model from config (reuse `models.coding`, overridable via `authoring.model`).

## Phases

Each phase is its own branch off `main` (`feat/authoring-0N-<name>`), draft PR opened after the
first commit with its plan linked in `Plans/`, tests + the cumulative smoke run before ready,
the user merges. Mirrors the branch-per-phase convention used through v0.1 and the deepening.

### Phase 1 — Candidate model + quarantine + manual `/author`

The core, mirroring GP Phase 16 (`/optimize`). A user describes a gap; Ubongo drafts a
validated, quarantined candidate skill.

- **1a — Package + candidate model.** `authoring/__init__.py`; `authoring/candidate.py` with a
  `SkillCandidate` dataclass (name, description, risk, reversibility, default_persona, body,
  prompts: dict[name->text], optional `command_template`, metadata).
- **1b — Drafting.** `draft_candidate(description) -> SkillCandidate` calls `llm.complete` with a
  Skill-Author system prompt that emits SKILL.md frontmatter + body + prompt template(s) +
  optional constrained-bash command shape. JSON-fenced output with defensive parsing (mirror the
  classifier/evaluator parse-tolerance).
- **1c — Validation.** `authoring/validation.py`: validate against `skills._parse_skill` schema
  (reuse the vocab constants), enforce the risk/reversibility floor for command skills, and run
  any `command_template` through the `sandbox.py` static checks without executing. Invalid ->
  rejected with a reason, never persisted.
- **1d — Quarantine.** Write the candidate to `config/skills_candidates/<name>/` (SKILL.md +
  prompts/), a directory `skills.py` does not scan. Persist a row in a new `authored_skills`
  table (id, name, description, status, generation, candidate JSON, paths, `backup_path`,
  created_at, decided_at) via `store.py` accessor pairs; `backup_path` is filled at approve time
  (Phase 3) when a prior version is backed up. `schema.sql` gets `CREATE TABLE IF NOT EXISTS`.
- **1e — Config + audit.** `authoring:` block in `config/settings.yaml` (`enabled`,
  `max_calls_per_hour`, `cron`, `model`, `promotion_margin` placeholder); `config.load_authoring()`.
  Add `"authoring"` to `vault._AUDIT_CATEGORIES`.
- **1f — REPL.** `/author <description>` (draft + preview) and `/skill-candidates` / `/skill-candidates list` (read-only listing). Handlers + registry entries + help banner.

**Files:** `src/ubongo/authoring/{__init__,candidate,validation}.py`, `src/ubongo/sandbox.py`
(expose static validator if needed), `src/ubongo/memory/{schema.sql,store.py}`,
`src/ubongo/memory/vault.py`, `src/ubongo/config.py`, `config/settings.yaml`, `src/ubongo/repl.py`.

**Tests:** candidate drafting (LLM patched), validation (schema + sandbox static + risk floor),
quarantine isolation (a drafted candidate is NOT returned by `skills.list_skills()`), store
accessor round-trip.

### Phase 2 — Candidate evaluation + quality score

Mirrors GP Phase 17 (`/evaluate`). Score a candidate side-effect-free so the gate can show a
number, never mutating the skill registry.

- **2a — Harness.** `authoring/sandbox.py::evaluate_candidate(candidate, samples, *, budget: CallBudget) -> CandidateMetrics`,
  no persistence inside. Prompt skills: run the candidate's prompt over a few held-out / synthetic
  user messages and judge with the existing 3-signal judge (quality, hallucination,
  would-correct). Command skills: static-validate, then a safe live dry-run of the command shape
  through the real `sandbox.run_constrained` (allowlisted, already safe), recording exit /
  refusal. Reuse `CallBudget` (all-or-nothing per candidate).
- **2b — Score.** Reuse `evolution/fitness.py::rank_cohort` shape (or a thin `authoring/fitness.py`
  wrapper) for a single quality scalar with deterministic tiebreak.
- **2c — Samples.** A small held-out / synthetic sample source for authored skills (extend
  `tests/manual/fixtures/` rather than inventing a new format).
- **2d — Surface.** `/author` prints an estimated quality; `/skill-candidates` shows the score.

**Files:** `src/ubongo/authoring/{sandbox,fitness}.py`, `src/ubongo/repl.py`,
`tests/manual/fixtures/` (additive).

**Tests:** harness produces no side effects (no skills registered, no DB rows beyond what the
caller writes), budget cap respected, prompt-skill scoring, command-skill static+dry-run path.

### Phase 3 — Approval gate: approve / reject / rollback + live registration

Mirrors GP Phase 19 promotions and `/improvements`. The human boundary.

- **3a — Promotion module.** `authoring/promotion.py`: `approve(id)` re-validates at the boundary
  (schema + risk floor), then, if `config/skills/<name>/` already exists, **backs it up** (3d)
  before materializing the quarantined folder into `config/skills/<name>/`, calls the
  `_bust_caches`-style reload (`skills.reload()` + `context.reload()`), writes an `authoring`
  audit row recording the backup path, marks status `approved`. `reject(id)` marks `rejected`,
  leaves quarantine.
- **3b — Backup + restore.** `authoring/promotion.py` (or a small `authoring/backup.py`):
  `backup_existing(name) -> Path | None` copies `config/skills/<name>/` to
  `config/skills_backups/<name>/<timestamp>/` via stdlib `shutil.copytree` and records the path on
  the `authored_skills` row. `rollback(name)` restores the most recent backup into
  `config/skills/<name>/` if one exists (otherwise removes the registered folder), reloads, audits,
  marks `rolled_back`. `config/skills_backups/` is gitignored.
- **3c — REPL gate.** `/skill-candidates approve <id> | reject <id> | rollback <name>` with a
  metadata + body/command diff preview (reuse `_diff_preview` / `_render_improvements_list`); the
  approve output notes when a prior version was backed up.
- **3d — Events.** Emit `authoring_candidate` (drafted) and `authoring_decision` (approved/
  rejected/rolled_back) on the event bus, for parity with `evolution_generation` /
  `evolution_promotion`.

**Files:** `src/ubongo/authoring/promotion.py` (+ optional `backup.py`), `src/ubongo/repl.py`,
`src/ubongo/events.py` (emit only), `.gitignore` (`config/skills_backups/`).

**Tests:** approve registers and the skill becomes discoverable by `skills.list_skills()` +
classifier-suggestable; approving over an existing skill creates a timestamped backup and the new
version is live; rollback restores the backed-up previous version byte-for-byte (and plain-removes
when there was no prior version); reject leaves it quarantined; risk floor re-enforced at approve;
audit rows present.

### Phase 4 — Autonomous authoring daemon (gap inference)

Mirrors GP Phase 18 (`EvolutionLoop`). The daemon drafts candidates on its own but still only
ever produces quarantined drafts; approval stays manual (the boundary never moves).

- **4a — Gap inference.** `authoring/gaps.py::next_gap()` — deterministic, bounded read over
  `messages` / `workflow_runs` / stored classifications to find recurring intents that had low
  classifier confidence or `suggested_skill = None` (capability the system keeps lacking).
- **4b — Loop.** `authoring/loop.py::AuthoringLoop` mirroring `EvolutionLoop`: boots paused,
  `_should_cycle` pure gate (status + rolling-hour budget + cron), daemon thread, one cycle =
  pick a gap, draft (Phase 1 core), evaluate (Phase 2), persist a quarantined draft. New
  `authoring_runs` (rolling-hour window + crash recovery) and `authoring_state` (status row)
  tables, mirroring `evolution_runs` / `evolution_state`.
- **4c — Control + wiring.** `/authoring [status|pause|resume|off]`. Start/stop the loop in the
  REPL `run()` try/finally alongside the GP loop and vault watcher; a `UBONGO_DISABLE_AUTHORING`
  off-switch for the test suite (mirror the evolution/vault-watch off-switches in `conftest.py`).

**Files:** `src/ubongo/authoring/{gaps,loop}.py`, `src/ubongo/memory/{schema.sql,store.py}`,
`src/ubongo/repl.py`, `tests/conftest.py`.

**Tests:** loop boots paused, gate respects status/budget/cron, gap inference deterministic on a
seeded DB, crash recovery re-evaluates an uncommitted draft, control commands flip state.

### Phase 5 — Docs, security review, smoke

- **5a — ADR + SECURITY.** New `docs/adr/0013-self-authored-skills-quarantine-and-approval.md`
  (the boundary, the risk floor, why the allowlist stays human-only). Extend `docs/SECURITY.md`
  with the authored-skill threat model.
- **5b — Living docs.** Update `STATE.md`, `STATUS.md`, `CONTEXT.md`, `README.md`, and
  `docs/ubongo-open-items.md` (move the item to Resolved/Built). Update the C4 component diagram
  for the new package.
- **5c — Smoke.** Add an authoring section to `tests/manual/smoke_test.md`: `/author` a skill ->
  `/skill-candidates` approve -> use it in a turn -> `/skill-candidates rollback`; and the daemon
  drafting a candidate from a seeded gap. Run the full cumulative smoke.

**Files:** `docs/adr/0013-*.md`, `docs/SECURITY.md`, `STATE.md`, `STATUS.md`, `CONTEXT.md`,
`README.md`, `docs/ubongo-open-items.md`, `docs/architecture/*`, `tests/manual/smoke_test.md`.

## Verification

- **Per phase:** `uv run pytest` green (new suites: `test_authoring_candidate`,
  `test_authoring_validation`, `test_authoring_sandbox`, `test_authoring_promotion`,
  `test_authoring_loop`, `test_repl_authoring`), plus the existing 778 stay green.
- **End to end (Phase 3+):** in the REPL, `/author "summarize a git diff into release notes"`,
  confirm it is quarantined and NOT in `/skills`; `/skill-candidates approve <id>`; confirm it now
  appears in `/skills` and is usable via `/skill <name>`; `/skill-candidates rollback <name>`;
  confirm it is gone. `/audit authoring` shows the rows.
- **Backup/restore:** author a skill that reuses an existing skill's name, approve it, confirm a
  `config/skills_backups/<name>/<timestamp>/` copy of the prior version exists and the new version
  is live; `rollback <name>` and confirm the prior version is restored intact.
- **Daemon (Phase 4):** `/authoring resume` on a DB seeded with a recurring unmet intent; within a
  cycle `/skill-candidates` shows a machine-drafted quarantined candidate; budget throttle holds.
- **Security:** a candidate whose command_template uses a non-allowlisted program, shell
  metacharacters, or path traversal is rejected at draft and again at approve; a command skill that
  declares `risk: low` is forced to medium/irreversible; the cumulative Phases 0-21 smoke still
  passes.
- **LOC budget:** stay mindful of the ~15k soft target (currently ~12.06k); the package mirrors
  `evolution/` in size, so expect a few thousand lines. Cut if it balloons.

## Out of scope (explicit)

- No changes to the `sandbox.py` allowlist. New executables remain a human code change.
- No auto-approval. The daemon drafts; only the user registers.
- No new first-class tools (ADR-0005 / CLAUDE.md: capabilities default to CLI scripts behind
  constrained-bash).
- Not a Telegram or web concern; this is core, channel-agnostic.
</content>
