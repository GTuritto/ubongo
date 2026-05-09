# Phase 1 — CLI REPL + One-Shot (echo): Implementation Plan

Date: 2026-05-09
Branch: `phase-1-cli-echo` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 686–719.

## Goal

`python -m ubongo` opens a REPL with the prompt `> `; each text turn echoes `[<persona>] <input>`. Slash commands `/architect`, `/operator`, `/casual`, `/auto`, `/exit` switch the active persona (or quit). One-shot mode `python -m ubongo send "<msg>" [--persona <name>]` runs a single turn and exits. Still no LLM. The smoke playbook gets its first populated sections.

## Why this plan exists

Phase 1 is small but it locks in three things later phases inherit: the REPL turn-loop shape (prompt → read → dispatch → respond → log), the slash-command parsing convention, and the one-shot CLI contract. The Phase 0 plan was heavy on cross-cutting decisions; this one is mostly about getting the I/O surface right.

## Branch + commit strategy

Branch already cut. Five commits, one per sub-phase, each green at HEAD. Final 1e commit message says "Phase 1 complete." Then a sixth tiny commit updates `STATUS.md` and populates the Phase 0 + Phase 1 sections of `tests/manual/smoke_test.md`. Six commits total.

## Sub-phases

### 1a — REPL loop

**Purpose:** A blocking input loop with the prompt `> `, a text-handler, a slash-handler, and clean exit on `/exit`, EOF (Ctrl+D), or SIGINT (Ctrl+C).

**Tasks:**

1. Write `src/ubongo/repl.py`:
   - `DEFAULT_PERSONA = "architect"`.
   - `VALID_PERSONAS = {"architect", "operator", "casual"}` (sourced from filesystem listing of `config/personas/*.md` would be cleaner, but Phase 1 hardcodes — Phase 6 introduces dynamic discovery for skills, and personas can follow the same path then).
   - `run(default_persona: str = DEFAULT_PERSONA) -> int`: the loop.
     - Print a brief "Ubongo REPL ready. /exit to quit." line on entry.
     - Loop: read input via `input("> ")` inside `try/except EOFError, KeyboardInterrupt` — both treated as clean exit (rc 0).
     - If line is empty after `.strip()`, continue.
     - If line starts with `/`, dispatch via `_handle_slash`.
     - Else dispatch via `_handle_text`.
   - `_handle_slash(cmd: str, current_persona: str) -> tuple[str, bool]`: returns `(new_persona, should_continue)`. Recognized commands listed in 1c.
   - `_handle_text(text: str, persona: str) -> None`: prints the echo line. Emits a `repl_turn` log event (persona, length, NOT the message body).
2. The REPL only writes "user-visible" text (echo, prompt, error messages) to **stdout**. JSON logs go to stderr via the existing logger setup.

**Files added:** `src/ubongo/repl.py`.

**Decision flagged:** Persona state lives as a local variable inside `run()`. No `Session` class yet — the spec's "files touched" list is strict (`repl.py`, `oneshot.py`, `__main__.py`), and Phase 4 (SQLite memory) is the natural place to lift session state into its own module. Don't over-build.

### 1b — One-shot command

**Purpose:** `python -m ubongo send "<msg>" [--persona <name>]` runs exactly one turn, prints the echo, and exits 0. Provides the scriptable interface promised in `UBONGO_BUILD.md`.

**Tasks:**

1. Write `src/ubongo/oneshot.py`:
   - `run(message: str, persona: str | None = None) -> int`:
     - If `persona` is None, use `repl.DEFAULT_PERSONA`.
     - If `persona` is not in `repl.VALID_PERSONAS`, print `Error: unknown persona '<name>'. Choose from: architect, operator, casual.` to stderr and return 1.
     - Print `[<persona>] <message>` to stdout.
     - Emit an `oneshot_turn` log event (persona, length).
     - Return 0.
2. No state, no loop. Pure function.

**Files added:** `src/ubongo/oneshot.py`.

**Decision flagged:** Validation of `--persona` happens in `oneshot.run`, not in argparse. Cleaner error messages than `argparse.ArgumentError`, and keeps the persona list as a single source of truth in `repl.py`.

### 1c — Slash command parser

**Purpose:** Map five slash commands to behaviors. Reject unknown slashes with a friendly error.

**Tasks:**

1. Inside `repl.py`'s `_handle_slash`:
   - `/architect`, `/operator`, `/casual`: switch active persona; print confirmation `Switched to <persona>.` to stdout. Return `(new_persona, True)`.
   - `/auto`: reset to `DEFAULT_PERSONA`. In Phase 1, `/auto` literally means "use the default" since classifier-driven routing doesn't exist until Phase 3. Print `Auto routing not yet active (Phase 3); using default persona: architect.` Return `(DEFAULT_PERSONA, True)`.
   - `/exit`: print `Goodbye.` Return `(persona, False)` to break the loop.
   - Anything else: print `Unknown command: /<rest>. Try /architect, /operator, /casual, /auto, /exit.` Return `(persona, True)`.
2. Slash parsing is whitespace-tolerant: `_handle_slash` strips and lowercases the command portion.

**Files modified:** `src/ubongo/repl.py` (this is the same file as 1a; logically separated for clarity, lands in 1a's commit if the boundary is awkward — see commit notes below).

**Decision flagged:** The `/auto` message explicitly tells the user that real auto-routing is a Phase 3 thing. Honest UI beats silent stubbing. When Phase 3 ships, this message gets removed and `/auto` triggers the classifier.

### 1d — Echo response

**Purpose:** Lock in the echo format `[<persona>] <input>` and the per-turn log event shape.

**Tasks:**

1. In `_handle_text` (repl) and `oneshot.run`:
   - Output: `print(f"[{persona}] {message}")` to stdout.
   - Log: `logger.info("repl_turn" or "oneshot_turn", extra={"persona": persona, "length": len(message)})`. **Do not** log the message body.
2. The echo format is canonical: every Phase 1 test depends on it being exactly `[<persona>] <text>` with one space after the bracket.

**Files modified:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py` (consolidated with 1a/1b).

**Decision flagged:** Logging the message length but not the body. By Phase 4 (memory) the body is persisted to SQLite and is recoverable from there; logs stay slim and don't double-store conversational content.

### 1e — `__main__.py` dispatch

**Purpose:** Wire `__main__.py` to call REPL or one-shot. Replace the Phase 0 no-op `send` branch with the real one.

**Tasks:**

1. Modify `src/ubongo/__main__.py`:
   - Add `--persona` arg to the `send` subparser. Choices not declared at argparse level (handled in `oneshot.run` for cleaner errors).
   - After `setup_logging` + `log_startup`:
     - If `args.command == "send"`: call `oneshot.run(args.message, args.persona)` and return its rc.
     - Else (no subcommand): call `repl.run()` and return its rc.
   - Imports added: `from ubongo import repl, oneshot`.
2. Keep the Phase 0 ConfigError handling intact.

**Files modified:** `src/ubongo/__main__.py`.

## Final file tree after Phase 1

```text
src/ubongo/
  __init__.py
  __main__.py    (modified — wires REPL and one-shot)
  config.py
  context.py
  logging.py
  repl.py        (new)
  oneshot.py     (new)
config/
  ...            (unchanged)
tests/manual/
  smoke_test.md  (modified — Phase 0 and Phase 1 sections populated)
STATUS.md        (modified — Phase 1 row → Complete)
Plans/
  phase-1-cli-echo.md  (new — this file)
```

Untouched: every other module the spec lists. Don't add `events.py`, `session.py`, `llm.py`, etc. yet.

## Testing plan (from the spec, made concrete)

| # | Scenario | Command / Steps | Expected |
| --- | --- | --- | --- |
| 1 | REPL echo with default persona | `uv run python -m ubongo`, type `hello` | stdout: `[architect] hello`. Then prompt returns. |
| 2 | Persona switch | After test 1: `/casual`, then `hello` | stdout: `Switched to casual.` then `[casual] hello`. |
| 3 | `/auto` resets to default | After test 2: `/auto`, then `hello` | stdout: Phase-3 notice, then `[architect] hello`. |
| 4 | One-shot with `--persona` | `uv run python -m ubongo send "hello" --persona operator` | stdout: `[operator] hello`. rc 0. |
| 5 | `/exit` | type `/exit` in REPL | stdout: `Goodbye.` rc 0. |

Plus two extra checks I'll run that aren't in the spec but are easy and worth covering before handoff:

| # | Extra | Command | Expected |
| --- | --- | --- | --- |
| 6 | Unknown slash | type `/foo` | `Unknown command: /foo. Try /architect, ...` Loop continues. |
| 7 | One-shot bad persona | `uv run python -m ubongo send "x" --persona bogus` | stderr: `Error: unknown persona 'bogus'. ...` rc 1. |
| 8 | EOF / Ctrl+D | press Ctrl+D in REPL | clean exit. rc 0. |

## Smoke test playbook updates

`tests/manual/smoke_test.md` currently has stubs for every phase. Phase 1 is where the playbook starts being run. Steps:

1. **Populate Phase 0 section** (was a stub): two scenarios — cold start prints JSON line; missing key prints plain-text error rc 1.
2. **Populate Phase 1 section** (was a stub): the five spec test scenarios from the table above. After the user merges, every future phase's smoke test will start with "do all sections from Phase 0 forward." This phase establishes that ritual.
3. **Add a top-of-file note**: cumulative scenarios; if any older scenario regresses, the phase is not complete.

## Out of scope for Phase 1 (do NOT build)

- LLM integration (Phase 2).
- Tone classifier / auto-routing (Phase 3).
- SQLite memory, sessions, history (Phase 4).
- Vault projection (Phase 5).
- Skills (Phase 6).
- Outbound queue (Phase 7).
- Master Agent and workers (Phase 8+).
- Multi-line input, paste handling, readline history (revisit if it becomes annoying — likely Phase 4).
- A `Session` class abstraction (Phase 4 introduces it).
- Tab completion for slash commands (deferred indefinitely; nice-to-have).
- Any pytest scaffolding (deferred to Phase 2 or whichever phase first has logic worth unit-testing — echo and slash dispatch are simple enough that the manual playbook covers them).

## Open questions to confirm before I start

1. **`/auto` Phase-1 semantics.** I'm planning to make `/auto` print a one-line notice ("Auto routing not yet active (Phase 3); using default persona: architect.") so the behavior is honest. Alternative: silent fallback — `/auto` just switches to architect with no message. The spec test 3 doesn't specify either. I prefer the explicit notice. OK?
2. **Default persona = `architect`.** Spec test 1 strongly implies it. Confirming.
3. **No pytest tests in Phase 1.** The five scenarios are manual-playbook scenarios. Adding pytest now would be one test file (`test_repl.py`) for slash parsing — small, but the spec doesn't list it. I'll skip pytest until a phase has logic that genuinely needs unit coverage. OK?
4. **REPL on entry: print a one-line banner** (`Ubongo REPL ready. /exit to quit.`) before the first prompt, or jump straight to `> `? I prefer the banner — clearer for first-time use. Spec doesn't say. OK?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 1

- Six commits on `phase-1-cli-echo` (1a–1e plus STATUS/smoke-test).
- `uv run python -m ubongo` opens a REPL; all five spec test scenarios pass.
- Three extra checks (unknown slash, bad persona, EOF) pass.
- `tests/manual/smoke_test.md` Phase 0 and Phase 1 sections populated.
- `STATUS.md` Phase 1 row → Complete (2026-05-09); LOC count updated.
- Branch handed to user for merge. Don't merge.
