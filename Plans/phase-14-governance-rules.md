# Phase 14 — Risk + Confidence Scoring: Implementation Plan

Date: 2026-05-16
Branch: `phase-14-governance-rules` (off `main` at `f6762ec`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 14 (lines 1099–1129).

## Context

`governance/decision.py` is still a Phase-10 stub: `decide()` rejects when evaluator
confidence is below 0.2 and returns `auto` for everything else. The `Action` enum
declares `ask_clarification` and `require_approval` but **nothing in the codebase
uses them** — master only handles `reject`. `governance_decisions.reversibility` is
a real column that is always written `NULL`. There is no `config/governance.yaml`;
thresholds are hardcoded constants (`REJECT_BELOW` in decision.py, `CRITIC_LOW/HIGH`
in master.py, with `# Phase 14 moves these` comments).

Phase 14 makes the decision matrix actually decide: three score modules
(risk / confidence / reversibility) feed a rule-driven `decide()` that returns one
of `auto | ask_clarification | require_approval | reject`, with all thresholds in
`config/governance.yaml`. Master acts on all four actions. End of result: a
destructive request gets gated, an under-specified command gets a clarification
ask, a low-confidence answer gets rejected, and a normal turn auto-approves —
visibly, in `/decisions`, `/trace`, and a new `/policy` command.

This is Tier 4 (Governance). Phase 15 builds the interactive approval y/n flow on
top of `require_approval`; Phase 14 only produces the decision and a blocking
message.

## Goal

A working decision matrix. `decide()` combines three scored signals via rules
loaded from `config/governance.yaml`:

- **risk** — `low | medium | high | destructive`, from the classifier's `risk`
  field, escalated by a keyword backstop (so "delete the entire vault" / "rm -rf"
  reliably reach `destructive` even when the small classifier model under-rates or
  flakes).
- **confidence** — the evaluator's `[0,1]` score (LLM-as-judge), classifier
  `confidence` as fallback when no evaluator ran. (Formalizes what master already
  computes as `stored_confidence`.)
- **reversibility** — `reversible | irreversible`. Irreversible when the workflow
  uses the `execution` agent (constrained-bash) or an irreversible skill; else
  reversible. (In v0.1 nearly every auto-routed turn is reversible — the module is
  the seam Phase 15/19/20 lean on.)

The matrix, evaluated in priority order (safety before quality before clarity):

| # | Condition | Action |
| --- | --- | --- |
| 1 | risk == `destructive` | **require_approval** |
| 2 | risk == `high` AND reversibility == `irreversible` | **require_approval** |
| 3 | evaluator confidence present AND < `reject_below_confidence` | **reject** |
| 4 | task_type == `command` AND classifier confidence < `clarification_below_confidence` | **ask_clarification** |
| 5 | else | **auto** |

Rules 1–2 are exactly the existing `settings.yaml::governance.approval_required_on:
[destructive, irreversible_high_risk]` — Phase 14 makes them act.

## Non-goals (locked)

- **The interactive approval prompt.** `require_approval` produces a *blocking
  message* in Phase 14. The y/n/why flow (`governance/approval.py`) is Phase 15.
- **Persona-voiced clarification.** `ask_clarification` produces a canned message
  (consistent with the existing `_REJECT_MESSAGE`). A persona-generated clarifying
  question would be an extra LLM call; defer to Phase 15 if wanted.
- **A new classifier field.** Ambiguity detection reuses the existing classifier
  `confidence` on `command` turns; no new classifier output, no classifier prompt
  change.
- **Per-intent rule variants.** One matrix for all intents in v0.1.
- **Chained-retry governance.** Phase 13's `requires_user_decision` repair path is
  untouched.

## Why this plan exists

Two patterns Phase 14 locks in:

1. **Governance config is data, not code.** Every threshold moves to
   `config/governance.yaml`. `decide()` reads rules; it does not embed them. The GP
   loop (Phase 16–19) can later evolve governance thresholds as an evolvable target
   without touching Python.
2. **Score modules are independent and individually testable.** `risk.py`,
   `confidence.py`, `reversibility.py` each take typed inputs and return a typed
   score with zero knowledge of the matrix. `decision.py` is the only module that
   knows how they combine. Phase 15+ can add a fourth signal by adding a module and
   one matrix rule.

## Branch + commit strategy

Branch `phase-14-governance-rules` off `main` at `f6762ec`. Per
`feedback_phase_branch_open_draft_pr`: push and open a **draft PR** immediately
after the Plan commit, base `main`, title `Phase 14 — Risk + Confidence Scoring`.

Eight commits (Plan + 14a–14g):

- **14a — `config/governance.yaml` + loader.** New `governance.yaml` holds the
  matrix thresholds, the `require_approval` rules, the destructive-keyword list,
  and the critic band. `config.py` gains `load_governance()` (mirrors
  `load_config()` — cached YAML read). The `governance:` block is **moved out of
  `settings.yaml`** into `governance.yaml` (single source; no duplicate config).
  Tests.
- **14b — `governance/risk.py`.** `RiskLevel` enum (`low/medium/high/destructive`,
  ordered). `score_risk(classification, message, rules) -> RiskLevel` = max of the
  classifier's `risk` and a keyword scan of `message` against
  `destructive_keywords`. Tests.
- **14c — `governance/confidence.py`.**
  `score_confidence(classification, workflow_result) -> float` — evaluator
  confidence if present, else classifier confidence. Tests.
- **14d — `governance/reversibility.py`.** `Reversibility` enum. `score_reversibility(workflow, classification) -> Reversibility` — `irreversible`
  if the workflow's agents include `execution` or its skill is marked irreversible;
  else `reversible`. Tests.
- **14e — `governance/decision.py` rewrite.** `decide()` keeps returning
  `Decision(action, reason)` but now: scores the three signals, loads
  `governance.yaml`, applies the 5-rule matrix. Signature gains `message` (needed
  for keyword risk) — one call-site update in `master.decide()`. `Decision` gains
  `risk`, `confidence`, `reversibility` fields so master can persist them without
  re-scoring. Rewrite `tests/test_governance_decision.py` (the stale
  `..._stub_does_not_gate` test flips to assert `require_approval`).
- **14f — `/policy` REPL command.** Prints the loaded matrix: thresholds,
  `require_approval` rules, destructive keywords. Parser + renderer in `repl.py`;
  add `/policy` to the help banner.
- **14g — master integration + `governance_decisions` writes.** `master.decide()`
  passes `message`; master persists the scored `reversibility` (no longer `None`)
  and `risk`/`confidence` from the `Decision`. Generalize the Phase-10 reject
  override into a `_GATED_MESSAGES` map covering `reject`, `ask_clarification`,
  `require_approval` (each overrides `result.text`, `ok=True`, zeroed tokens — the
  existing reject pattern). `CRITIC_LOW/HIGH` read from `governance.yaml`.
  `/decisions` and `/trace` renderers show the `reversibility` column. Tests.
- **STATUS + smoke playbook Phase 14 section.**

## Sub-phase detail

### 14a — `config/governance.yaml` + loader

`config/governance.yaml`:
```yaml
# Governance decision matrix. decide() reads these rules; thresholds are data.
thresholds:
  reject_below_confidence: 0.2          # evaluator confidence floor -> reject
  clarification_below_confidence: 0.5   # classifier confidence floor on command turns
  critic_band: [0.2, 0.6]               # borderline evaluator band -> Critic re-dispatch
require_approval:
  risks: [destructive]                  # these risk levels always require approval
  irreversible_high_risk: true          # risk=high AND reversibility=irreversible
destructive_keywords:                   # message match escalates risk to destructive
  - "rm -rf"
  - "delete the entire"
  - "delete all"
  - "wipe"
  - "drop table"
  - "drop database"
  - "format "
  - "truncate"
```
`config.py::load_governance(path=...)` — cached YAML read mirroring `load_config()`.
Remove the `governance:` block from `settings.yaml` (its two keys are subsumed:
`approval_required_on` → `require_approval`, `confidence_threshold_for_auto` is
unused dead config). Files: `config/governance.yaml` (new), `config/settings.yaml`,
`src/ubongo/config.py`, `tests/test_config.py`.

### 14b — `governance/risk.py`

`RiskLevel(str, Enum)` low<medium<high<destructive with an ordering helper.
`score_risk(classification, message, rules) -> RiskLevel`: parse the classifier's
`risk` string; scan lowercased `message` for any `destructive_keywords` entry; return
the higher. Keyword hit → `destructive`. Files: `src/ubongo/governance/risk.py`,
`tests/test_governance_risk.py`.

### 14c — `governance/confidence.py`

`score_confidence(classification, workflow_result) -> float`: return
`workflow_result.evaluator_confidence` if not None, else `classification.confidence`.
Files: `src/ubongo/governance/confidence.py`, `tests/test_governance_confidence.py`.

### 14d — `governance/reversibility.py`

`Reversibility(str, Enum)` = `reversible | irreversible`.
`score_reversibility(workflow, classification) -> Reversibility`: `irreversible` if
`"execution" in workflow.agents`, or the workflow's skill is registered with
`reversibility="irreversible"` (read via `skills.py`); else `reversible`. Files:
`src/ubongo/governance/reversibility.py`, `tests/test_governance_reversibility.py`.

### 14e — `governance/decision.py` rewrite

`decide(classification, workflow_result, *, message, evaluator_confidence=None) ->
Decision`. Scores risk/confidence/reversibility, loads `governance.yaml`, applies the
5-rule matrix, returns `Decision(action, reason, risk, confidence, reversibility)`.
`reason` strings stay machine-parseable (e.g. `risk_destructive`,
`evaluator_confidence_below_floor:0.12`, `command_low_classifier_confidence:0.40`).
Rewrite `tests/test_governance_decision.py` — one matrix-cell test per rule plus
edge cases; flip `test_decide_returns_auto_for_high_risk_destructive_stub_does_not_gate`.

### 14f — `/policy` REPL command

`_render_policy()` reads `load_governance()` and prints thresholds, require_approval
rules, destructive keywords. Wire `/policy` into the REPL command dispatch + help
banner. Files: `src/ubongo/repl.py`, `tests/test_repl_policy.py`.

### 14g — master integration + governance_decisions writes

In `master.py`: `decide()` passes `message=` to `governance_decide`. Replace the
`if rejected:` block with a `_GATED_MESSAGES: dict[str,str]` lookup —
`reject → _REJECT_MESSAGE`, `ask_clarification → _CLARIFICATION_MESSAGE`,
`require_approval → _APPROVAL_REQUIRED_MESSAGE`; any gated action overrides
`result.text`, sets `ok=True`, zeroes tokens (the current reject behavior,
generalized). `append_governance_decision(...)` gets `reversibility=decision.reversibility`
and `risk`/`confidence` from the `Decision`. `CRITIC_LOW/HIGH` read from
`load_governance()["thresholds"]["critic_band"]`. `/decisions` + `/trace` renderers
add a `rev=` field; `store.last_n_governance_decisions` / `last_n_workflow_runs`
SELECT `reversibility`. Files: `src/ubongo/master.py`, `src/ubongo/memory/store.py`,
`src/ubongo/repl.py`, `tests/test_master.py`, `tests/test_repl_decisions.py`,
`tests/test_repl_trace.py`.

## Files

New: `config/governance.yaml`, `src/ubongo/governance/{risk,confidence,reversibility}.py`,
`tests/test_governance_{risk,confidence,reversibility}.py`, `tests/test_repl_policy.py`.
Modified: `config/settings.yaml`, `src/ubongo/config.py`,
`src/ubongo/governance/decision.py`, `src/ubongo/master.py`,
`src/ubongo/memory/store.py`, `src/ubongo/repl.py`, `tests/test_governance_decision.py`,
`tests/test_config.py`, `tests/test_master.py`, `tests/test_repl_decisions.py`,
`tests/test_repl_trace.py`, `STATUS.md`, `tests/manual/smoke_test.md`.
Schema: none — `governance_decisions.reversibility` already exists.

## Testing plan (spec §Phase 14)

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Auto-approve | casual question | `decide()` → `auto`; `governance_decisions.action='auto'`. |
| 2 | Reject low confidence | evaluator confidence forced < 0.2 (pytest) | `reject`; response is `_REJECT_MESSAGE`. |
| 3 | Ask clarification | `command` turn, low classifier confidence (pytest matrix test) | `ask_clarification`; response is `_CLARIFICATION_MESSAGE`. |
| 4 | Require approval | `ubongo send "delete the entire vault"` | keyword backstop → risk `destructive` → `require_approval`; response is `_APPROVAL_REQUIRED_MESSAGE`; the real answer is not delivered. |
| 5 | `/policy` | REPL `/policy` | prints thresholds + require_approval rules + destructive keywords. |

Smoke playbook gets a Phase 14 section (14.1–14.x) appended. Full pytest suite stays
green (454 + ~30 new governance/matrix/policy tests).

## Open questions (defaults below; push back to change)

1. **`governance.yaml` replaces the `settings.yaml::governance` block** — single
   config home, no duplication. The two old keys are subsumed/dead. OK?
2. **Matrix precedence is safety-first** — `require_approval` (destructive /
   high+irreversible) outranks `reject` (low confidence). A destructive request is
   gated for the user to see, not silently rejected. OK?
3. **`ask_clarification` heuristic** = `task_type == "command"` AND classifier
   `confidence < 0.5`. v0.1 reuses the classifier's own confidence as the
   ambiguity proxy; no new classifier field. OK?
4. **`ask_clarification` / `require_approval` responses are canned messages** in
   Phase 14 (like `_REJECT_MESSAGE`); persona-voiced clarification and the
   interactive y/n approval are Phase 15. OK?
5. **Keyword backstop in `risk.py`** + `message` added to `decide()`'s signature
   (one call-site change) — so scenario 4 is deterministic and not at the mercy of
   the small classifier model. OK?
6. **Reversibility v0.1**: `irreversible` only when the workflow uses the
   `execution` agent or an irreversible skill; every other turn `reversible`. OK?

## Definition of done

- 8 commits on `phase-14-governance-rules` (Plan + 14a–14g); draft PR opened after
  the Plan commit.
- `decide()` returns all four actions per the matrix; master acts on all four.
- `governance_decisions.reversibility` is populated; `/decisions` and `/trace` show it.
- `/policy` prints the live matrix.
- Testing-plan scenarios 1–5 pass; full pytest suite green.
- `STATUS.md` Phase 14 row → Complete; smoke playbook Phase 14 section appended.
- Branch handed over for merge on your say-so.
