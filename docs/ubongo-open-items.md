# Ubongo: Open Items

Written against STATE.md as of 2026-06-09. This replaces an earlier ideas list that
predated the current tree. Most of what that list proposed is already built. What follows
is only what genuinely remains, plus a record of what was dropped and why, so the
reconciliation is visible rather than silent.

## What was dropped, and why

- **Workflows / scheduled routines.** Already accounted for. Proactive output is the v0.3
  queue seam, deliberately left unwired. Nothing to add.
- **Autonomous multi-step planning.** Already built. It is the Master pipeline
  (classify, plan, execute, govern, compose, enqueue). The earlier "defer this" advice is moot.
- **Skills via MCP.** Retracted as a blanket suggestion. The project already has a skills layer
  with a deliberate discipline: CLI scripts behind a constrained-bash sandbox, enforcement in
  `sandbox.py`, per ADR-0005 and a CLAUDE.md rule. MCP is a different trust surface and would
  reopen a sandboxing question that is already closed. It earns a place only where a capability
  genuinely cannot fit the CLI-script shape, and even then how it sits relative to the existing
  sandbox is a fresh decision. Not an idiomatic default.
- **Difficulty-aware model selection.** Verified resolved on 2026-06-09 (see Resolved, below).
  It was the "verify first" item; the verification came back done, so it is no longer open.

## Resolved

### Model selection is difficulty-aware (verified 2026-06-09)

The vision wants per-task model selection by cost and capability: cheap model for classification,
strong model for the hard reply. The check was whether that is real or whether every agent is
pinned to one model. The answer is both, and that is correct.

Each agent carries a fixed `default_model` from `config/settings.yaml`, but the model the user
gets per turn is chosen one layer up, by the classifier's routing. The classifier (cheap
`qwen-2.5-7b`) routes intent to a workflow to a persona/agent set, and the tiers line up with the
vision: casual persona and compaction on `haiku-4.5`; architect/operator personas and the
Research/Coding/Evaluator/Critic agents on `sonnet-4.5`. So "ugh long day" lands on casual/haiku
and a circuit-breaker question lands on architect/sonnet. That is difficulty-driven selection,
expressed through role rather than through a separate difficulty-to-model knob.

`agents/llm_run.py` confirms the envelope is deliberately mechanical: `resolve_model` returns
`input.directives.override_model or default_model` and nothing else. `override_model` is set only
by the Repair ladder (RETRY_DIFFERENT_MODEL / RETRY_SMALLER_MODEL) and the evolution
sandbox/config overrides; normal turns pass `override_model=None`. ADR-0012 states the envelope is
boilerplate removal, not a decision point.

The earlier note proposed, as a fallback, making the envelope select by difficulty. That is the
wrong layer, not just unneeded work: a difficulty-to-model decision inside the envelope would
duplicate and fight the classifier's job and breach the ADR-0003 pipeline. If difficulty-awareness
ever needs sharpening, the correct layer is the classifier/routing rules, which the GP loop can
already evolve via the `routing:default` target.

## What actually remains

### 1. New-capability authoring (the real self-extension gap)

The GP loop already provides governed self-improvement: it evolves prompts and configs,
evaluates in a sandbox, boots paused, and promotes nothing without approval. What it does not
do is author brand-new skills or integrations. It optimizes what already exists.

So the only thing a self-extension experiment would actually add, beyond what is built, is
Ubongo authoring new capabilities rather than tuning current ones. That is a narrow, specific
experiment, not the broad "let it grow on its own" framing from earlier. If pursued, it should
inherit the patterns already in place: contained environment, full logging, and the existing
approval gate as the boundary between the experiment and real credentials. Distinct from the GP
loop, lower priority, and only worth doing if the itch is specifically to watch it invent
capabilities you would not have specified.

## External references worth consulting

Goose (github.com/aaif-goose/goose) is a mature general-purpose agent in the same category as
Ubongo. It is Rust, so nothing transfers as code, only as patterns. Two parts are worth reading
when the related work comes up, not before:

- **MCP governance.** If the skills-via-MCP question (see Dropped, above) is ever reopened, Goose
  is the reference case: it runs 70+ MCP extensions, so its SECURITY.md and extension permission
  model are the cheapest way to pressure-test whether the CLI-scripts-behind-sandbox discipline in
  ADR-0005 still holds. Read it before changing ADR-0005, not as a reason to change it.
- **Recipe / workflow format.** When the v0.3 proactive-jobs seam grows into a real workflow
  format, Goose's recipes (CONTRIBUTING_RECIPES.md, `workflow_recipes/`) are a battle-tested schema
  for parameterized reusable workflows. Reference for prior art, not a format to adopt wholesale.

Neither is a build task. Both are pointers so the relevant work starts from prior art.

## Net

The "verify first" item resolved to done, so the only open item is the new-capability experiment,
and that one is optional. The project is past the stage where an ideas list adds value; the useful
artifacts now are STATE.md and the ADRs. The Goose pointers above are reference material, not work
items.
