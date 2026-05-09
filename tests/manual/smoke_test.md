# Ubongo — Manual Smoke Test Playbook

This playbook is the cumulative end-to-end manual test for Ubongo. After every implementation phase is complete, walk through every section that exists. New phases append a new section.

The point: at every phase boundary, the *entire* Ubongo system should still work. New features land additively; old features must not regress.

Each section's scenarios are run in order. If a scenario fails, the phase is not complete.

## How to use

1. Make sure you're on `main` with the latest merge.
2. Set up: `uv sync`; `cp .env.example .env`; set `OPENROUTER_API_KEY`.
3. From section "Phase 0" forward, work through every section that's present.
4. Mark each scenario pass/fail in your local copy. Do not commit pass/fail marks; they're per-run.

## Phase 0 — Skeleton

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 0.1 | Cold start | `uv run python -m ubongo` (with `OPENROUTER_API_KEY` set in `.env`); pipe `/exit` to stdin so the REPL exits | One JSON line on stderr with `event="startup"`, `level="INFO"`, ISO8601 `ts`, redacted config summary. rc 0. |
| 0.2 | Missing API key | Move `.env` aside (`mv .env .env.bak`); `unset OPENROUTER_API_KEY`; `uv run python -m ubongo`; restore `.env` | rc 1; stderr: `Error: OPENROUTER_API_KEY not set. Copy .env.example to .env and fill it in.`; no traceback. |
| 0.3 | Hierarchical context assembly | `uv run python -c "from ubongo.context import build_system_prompt; print(build_system_prompt('architect'))"` | Output begins with `# UBONGO.md` body, blank line, then architect persona body (frontmatter stripped). |
| 0.4 | Log structure | `uv run python -m ubongo 2>&1 1>/dev/null \| jq .` (pipe `/exit` to stdin) | Valid JSON; has `event`, `level`, `ts`. `OPENROUTER_API_KEY` value is not present in the output. |

## Phase 1 — CLI REPL + One-Shot (echo)

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1.1 | REPL banner + default echo | `uv run python -m ubongo`; type `hello` | First line: `Ubongo REPL ready. /exit to quit.` Then a `>` prompt. Echo: `[architect] hello`. |
| 1.2 | Persona switch | After 1.1: `/casual`, then `hello` | `Switched to casual.` then `[casual] hello`. |
| 1.3 | `/auto` reverts to default | After 1.2: `/auto`, then `hello` | `Auto routing not yet active (Phase 3); using default persona: architect.` then `[architect] hello`. |
| 1.4 | `/exit` clean quit | type `/exit` | `Goodbye.` rc 0. |
| 1.5 | One-shot with `--persona` | `uv run python -m ubongo send "hello" --persona operator` | stdout: `[operator] hello`. rc 0. |
| 1.6 | One-shot default persona | `uv run python -m ubongo send "hi"` | stdout: `[architect] hi`. rc 0. |
| 1.7 | Unknown slash command | In REPL: `/foo` | `Unknown command: /foo. Try /architect, /operator, /casual, /auto, /exit.` Loop continues. |
| 1.8 | One-shot bad persona | `uv run python -m ubongo send "x" --persona bogus` | stderr: `Error: unknown persona 'bogus'. Choose from: architect, operator, casual.` rc 1. |
| 1.9 | EOF (Ctrl+D) | In REPL: press Ctrl+D | `Goodbye.` rc 0. |
| 1.10 | Pytest passes | `uv run pytest tests/test_repl.py` | All tests pass. |

## Phase 2 — LLM Integration

*(Populated when Phase 2 is implemented.)*

## Phase 3 — Tone Classifier + Auto Routing

*(Populated when Phase 3 is implemented.)*

## Phase 4 — SQLite Memory + Compaction

*(Populated when Phase 4 is implemented.)*

## Phase 5 — Markdown Vault Projection

*(Populated when Phase 5 is implemented.)*

## Phase 6 — Skills + Progressive Disclosure

*(Populated when Phase 6 is implemented.)*

## Phase 7 — Minimal Outbound Queue

*(Populated when Phase 7 is implemented.)*

## Phase 8 — Master Agent

*(Populated when Phase 8 is implemented.)*

## Phase 9 — First Workers (Research + Memory)

*(Populated when Phase 9 is implemented.)*

## Phase 10 — Evaluator + Critic + Persona Agents

*(Populated when Phase 10 is implemented.)*

## Phase 11 — Coding + Execution + Repair Agents

*(Populated when Phase 11 is implemented.)*

## Phase 12 — Execution Modes (all six)

*(Populated when Phase 12 is implemented.)*

## Phase 13 — Repair Agent Activated

*(Populated when Phase 13 is implemented.)*

## Phase 14 — Risk + Confidence Scoring

*(Populated when Phase 14 is implemented.)*

## Phase 15 — Approval Gates + Sandboxing

*(Populated when Phase 15 is implemented.)*

## Phase 16 — Variant Generation

*(Populated when Phase 16 is implemented.)*

## Phase 17 — Sandboxed Evaluation + Fitness

*(Populated when Phase 17 is implemented.)*

## Phase 18 — GP Loop (autonomous)

*(Populated when Phase 18 is implemented.)*

## Phase 19 — GP Targets Expanded + Promotions

*(Populated when Phase 19 is implemented.)*

## Phase 20 — Embeddings + Graph

*(Populated when Phase 20 is implemented.)*

## Phase 21 — Bidirectional Vault Sync + Audit

*(Populated when Phase 21 is implemented.)*
