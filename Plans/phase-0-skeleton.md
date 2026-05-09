# Phase 0 — Skeleton: Implementation Plan

Date: 2026-05-09
Branch: `phase-0-skeleton` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 657–684, plus referenced schemas (lines 165–296, 481–553).

## Goal

`uv run python -m ubongo` loads config, sets up structured JSON logging, prints one startup log line, and exits cleanly with rc 0. Pure scaffolding. No LLM, no REPL loop, no memory. The harness must be solid because every later phase plugs into it.

## Why this plan exists

Phase 0 looks trivial but locks in cross-cutting decisions (package layout, config-loading shape, logger formatter, context-loader signature, CLI argparse skeleton) that every subsequent phase inherits. Five of the eight `src/ubongo/` files added here (`__main__`, `__init__`, `config`, `context`, `logging`) live for the whole project. Getting the seams right now avoids churn later.

## Branch + commit strategy

- Create `phase-0-skeleton` from `main` at phase start. Don't commit to `main`.
- One commit per sub-phase (0a–0e), each green at HEAD. Five commits total.
- Final commit message includes "Phase 0 complete" so the merge later is easy to find.
- After all 4 testing-plan scenarios pass, hand off to user for review and merge. Do not merge.

## Sub-phases

### 0a — Project init

**Purpose:** A `uv sync`-able Python project with the right dependencies, src layout, and entry point.

**Tasks:**

1. Decide Python floor: `>=3.11` (matches README setup instructions).
2. Write `pyproject.toml` with:
   - `[project]` name `ubongo`, version `0.1.0`, requires-python `>=3.11`.
   - Dependencies: `litellm`, `python-dotenv`, `pyyaml`, `sqlite-vec`.
   - Dev dependencies: `pytest`.
   - `[build-system]`: hatchling (uv default) — package src layout via `[tool.hatch.build.targets.wheel] packages = ["src/ubongo"]`.
   - `[project.scripts]` optional; primary invocation is `python -m ubongo` per spec, so a console-script is not required for Phase 0.
3. Create `src/ubongo/__init__.py` (empty, with `__version__ = "0.1.0"`).
4. Create `.env.example` from the spec block (lines 485–497 of UBONGO_BUILD.md) verbatim.
5. Run `uv sync` and confirm a lockfile is generated.

**Files added:** `pyproject.toml`, `src/ubongo/__init__.py`, `.env.example`.
**Files modified:** none. (Existing `.gitignore` already covers `.env`, `vault/`, `__pycache__`, `.venv/`, `*.db`.)

**Decision flagged:** I'm not adding `ruff` / `mypy` / `pytest-asyncio` in Phase 0. They land when the first real code arrives that benefits (Phase 1 brings pytest tests; Phase 2+ brings async). Keeps Phase 0 minimal.

### 0b — Config loading

**Purpose:** A `config.load_config()` function that reads `config/settings.yaml`, resolves any `${ENV_VAR}` references against the environment (loaded from `.env`), and validates that required fields are present.

**Tasks:**

1. Write `config/settings.yaml` from the spec block (lines 501–553) verbatim.
2. Write `config/UBONGO.md` — the global identity file. Seed with the conventions section from `CLAUDE.md` (direct prose, no hedging, no em-dashes, no emojis, minimal markdown) plus a brief identity stanza. ~30 lines, user-editable.
3. Write `config/personas/architect.md`, `operator.md`, `casual.md` — short stubs (frontmatter `name`/`description` + a 2-3 line voice description). Phase 1+ uses them; Phase 0 just needs the files to exist for the context loader test.
4. Write `src/ubongo/config.py`:
   - `load_dotenv()` from `python-dotenv` at import time (only once).
   - `load_config(path: Path | None = None) -> dict`: reads YAML, walks the tree, replaces `${ENV_VAR}` strings with `os.environ[var]`. The `api_keys.openrouter.env: OPENROUTER_API_KEY` indirection in the spec is the canonical example.
   - Validation: confirm `OPENROUTER_API_KEY` is set in env (resolvable via the `api_keys.openrouter.env` field). Raise `ConfigError` with a clear message naming the missing var. Exit code 1, no traceback.
   - Cache the loaded dict (module-level) so repeated calls in Phase 0 don't re-read.
5. Module-level `ConfigError(Exception)`.

**Files added:** `config/settings.yaml`, `config/UBONGO.md`, `config/personas/{architect,operator,casual}.md`, `src/ubongo/config.py`.

**Decisions flagged:**
- **Validation depth:** Phase 0 validates only that `OPENROUTER_API_KEY` resolves. Validating model strings, paths, weights, etc. is deferred to whichever later phase first uses them (e.g., Phase 2 validates `models.default`). Avoids over-engineering before there's a consumer.
- **Schema library:** not using pydantic. A plain dict + targeted assertions is enough through Phase 1. If config validation grows hairy in Phase 4+ I'll revisit.
- **Env reference syntax:** `${VAR}`. I will not support default values (`${VAR:-default}`) in Phase 0.

### 0c — Hierarchical context loader

**Purpose:** `build_system_prompt(persona, skill=None, agent_role=None) -> str`. Reads the global identity, persona body, optional skill body, optional agent role frame; concatenates with double newlines.

**Tasks:**

1. Write `src/ubongo/context.py` with:
   - `_cache: dict[str, str]` for read-once files.
   - `_strip_frontmatter(text: str) -> str`: drops a leading `---\n...\n---\n` block if present. Personas have frontmatter per spec ("skipping frontmatter").
   - `_read_cached(path: Path) -> str`: reads + strips frontmatter once.
   - `build_system_prompt(persona: str, skill: str | None = None, agent_role: str | None = None) -> str`:
     1. Read `config/UBONGO.md` body.
     2. Read `config/personas/{persona}.md` body (frontmatter stripped).
     3. If `skill`: read `config/skills/{skill}/SKILL.md`, prefix with `## Active Skill: {skill}`. (No skills exist yet — Phase 6 ships them. The branch is here so the signature is stable.)
     4. If `agent_role`: append `## Agent Role: {agent_role}`. (No agents yet — Phase 8+. Same reason.)
     5. Join with `"\n\n"` and return.
   - `reload() -> None`: clears `_cache`. /reload command lands later; the function is here now.
2. Raise `FileNotFoundError` with a clear message if persona is missing. No silent fallback.

**Files added:** `src/ubongo/context.py`.

**Decision flagged:** The skill and agent-role branches are unreachable in Phase 0 (no skills, no agent roles yet). Keeping them now means Phase 6 and Phase 8 don't have to widen this function's signature. Tested only for the persona path in Phase 0.

### 0d — Structured JSON logging

**Purpose:** `setup_logging(level: str)` configures the root logger to emit one-line JSON to stderr. Other modules call `logging.getLogger("ubongo.<area>")` normally.

**Tasks:**

1. Write `src/ubongo/logging.py`:
   - Custom `JsonFormatter(logging.Formatter)`: emits `{"ts": ISO8601, "level": "INFO", "event": record.name or record.msg, ...record.__dict__ extras}`. Strip standard noise (`args`, `exc_info` handled separately, `pathname`, etc.).
   - `setup_logging(level: str = "INFO") -> None`: removes existing handlers on the root, adds one StreamHandler(stderr) with JsonFormatter, sets the level.
   - `log_startup(config: dict) -> None`: emits one log line with `event=startup` and a redacted summary (model names, vault path, evolution.enabled flag) — never API keys or env values.
2. Note: file is named `src/ubongo/logging.py`. In Python 3 absolute-import default, `import logging` inside this file resolves to the stdlib, so no shadow issue. Consumers import as `from ubongo.logging import setup_logging` to avoid ambiguity.

**Files added:** `src/ubongo/logging.py`.

**Decisions flagged:**
- **No `python-json-logger` dependency.** A 20-line custom formatter is enough and avoids dragging in another package for Phase 0.
- **Redaction policy:** I'll write `_redact(config)` that whitelists keys safe to log (`models.*`, `memory.recall_turns`, `vault.path`, `logging.level`, `evolution.enabled`). Anything not whitelisted is omitted. Safer than blacklisting.

### 0e — CLI entry

**Purpose:** `python -m ubongo` runs end-to-end: dotenv → config → logging → startup line → exit 0. `python -m ubongo send "..."` parses but is a no-op (real handling lands in Phase 1).

**Tasks:**

1. Write `src/ubongo/__main__.py`:
   - `main(argv: list[str] | None = None) -> int`:
     - argparse: top-level no args = default action; subparser `send` with positional `message`.
     - Wraps `load_config()` in try/except `ConfigError`: prints the error to stderr (plain text, not JSON to keep the missing-key error human-readable) and returns 1.
     - On success: `setup_logging(cfg["logging"]["level"])`, then `log_startup(cfg)`. If `send` was passed, log a `cli_send_received` event (no-op handling) and return 0. Otherwise return 0.
   - `if __name__ == "__main__": sys.exit(main())`.
2. Sanity-check that `uv run python -m ubongo` works from a fresh checkout when `OPENROUTER_API_KEY` is set, and fails cleanly when unset.

**Files added:** `src/ubongo/__main__.py`.

**Decision flagged:** Phase 0 returns early after the startup line. There is no REPL loop, no input handling. Phase 1 is where `python -m ubongo` becomes interactive.

## Final file tree after Phase 0

```text
ubongo/
  pyproject.toml                       (new)
  uv.lock                              (new, generated)
  .env.example                         (new)
  src/ubongo/
    __init__.py                        (new)
    __main__.py                        (new)
    config.py                          (new)
    context.py                         (new)
    logging.py                         (new)
  config/
    UBONGO.md                          (new)
    settings.yaml                      (new)
    personas/
      architect.md                     (new)
      operator.md                      (new)
      casual.md                        (new)
```

Untouched in Phase 0: any `agents/`, `governance/`, `evolution/`, `memory/`, `delivery/` directory; `repl.py`, `oneshot.py`, `events.py`, `llm.py`, `master.py`, `classifier.py`, `router.py`, `runner.py`, `composer.py`, `skills.py`; `routing.yaml`, `workflows.yaml`, `governance.yaml`, `urgency.yaml`; `tests/` Python tests.

## Testing plan (from UBONGO_BUILD.md, made concrete)

These are run manually against the `phase-0-skeleton` branch with a real `.env` containing `OPENROUTER_API_KEY=...` (any string works for Phase 0; we don't call the API yet).

| # | Scenario | Command | Expected |
| --- | --- | --- | --- |
| 1 | Cold start | `uv run python -m ubongo` | One JSON line on stderr with `event="startup"`, `level="INFO"`, `ts` ISO8601, redacted config summary. Exit code 0. |
| 2 | Missing API key | `OPENROUTER_API_KEY= uv run python -m ubongo` (or comment out in `.env`) | rc 1; stderr has `Error: OPENROUTER_API_KEY not set` (or similar) — plain text, no traceback. |
| 3 | Context assembly | `uv run python -c "from ubongo.context import build_system_prompt; print(build_system_prompt('architect'))"` | Output: `UBONGO.md` body, blank line, `architect.md` body (frontmatter stripped). |
| 4 | Log structure | `uv run python -m ubongo 2>&1 1>/dev/null \| jq .` | Valid JSON; has `event`, `level`, `ts`. No keys named `OPENROUTER_API_KEY`, `api_key`, or values that look like sk-/keys. |

All four pass, then I stop and hand to the user. The smoke playbook (`tests/manual/smoke_test.md`) is N/A for Phase 0 per spec — it starts in Phase 1.

## Out of scope for Phase 0 (do NOT build)

- Any LLM call (Phase 2).
- REPL loop, stdin reading, slash commands (Phase 1).
- SQLite, memory, vault, queue (Phases 4, 5, 7).
- Master Agent, workers, governance, evolution (Phases 8+).
- pydantic / config schema validation beyond "OPENROUTER_API_KEY resolves".
- Logging to file, log rotation, or anything beyond JSON-to-stderr.
- Tests in `tests/` — pytest scaffolding lands when there's something worth testing (Phase 1 likely).
- `uv.lock` review beyond confirming it generates. Pinning policy is a Phase-1 conversation.

## Open questions to confirm before I start

1. **Python version floor.** I'm planning `>=3.11`. README says "Python 3.11+". OK to lock to 3.11 in `pyproject.toml`?
2. **Hatchling vs setuptools.** `uv init --package` defaults to hatchling. OK to use it (no preference reason to override)?
3. **Persona stubs.** Architect / Operator / Casual all need *some* body text in Phase 0 so the context-loader test produces meaningful output. I'll write a 3-5 line voice description per persona — terse, in the v0.1 voice, easy to overwrite later. OK as a placeholder? Or do you want to write them yourself before Phase 1?
4. **`UBONGO.md` (global identity).** Seed content: a "who I am / how I want Ubongo to talk to me" stanza (keyed to the Conventions section already in `CLAUDE.md`) plus a placeholder for personal context (role, goals) you can fill in later. Acceptable as a starting point?

If you don't push back on any of these, I'll go with the defaults above.

## Definition of done for Phase 0

- All five sub-phase commits on `phase-0-skeleton`.
- `uv sync` clean. `uv run python -m ubongo` exits 0 with a valid JSON startup line.
- All four testing-plan scenarios pass on the branch.
- `STATUS.md` Phase 0 row updated from "Not started" → "Complete (2026-05-09)".
- No code under `src/ubongo/` other than the five files listed.
- Branch handed to user for merge. I do not merge.
