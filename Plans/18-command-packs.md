# Phase 18 — Let each subsystem own its command pack

Branch: `improve/18-command-packs`. Lifted from
[Plans/14-19-architecture-deepening-roadmap.md](14-19-architecture-deepening-roadmap.md).
Strength: **Worth exploring**. Behavior-neutral: **no VERSION bump**.

## Problem

`repl.py` (~1,470 lines) holds 66 `_parse_*`/`_render_*`/`_cmd_*` functions for
five subsystems; every subsystem change edits the file that owns the turn
loop. The registry seam (`commands.py`, candidate 04) already makes a command
a dict entry — the clusters just never moved out.

## Solution

Three subsystem packs, each a `commands.py` inside its package, exporting a
registry fragment plus its parsers/renderers/handlers:

- `evolution/commands.py` — `/optimize`, `/evaluate`, `/evolution`,
  `/improvements` (~370 lines).
- `authoring/commands.py` — `/author`, `/authoring`, `/skill-candidates`
  (~290 lines).
- `memory/commands.py` — `/recall`, `/audit`, `/conflicts` (~140 lines).

`repl.py` assembles `COMMANDS` by merging the fragments **in the original key
order** (the help banner derives from registry order and stays byte-identical)
and keeps the loop, prompts, `emit`, persona fallback — and, deliberately, the
REPL's own inspection surface.

Mechanics that make it behavior-neutral:

- **Late-bound help banner**: pack usage strings resolve `_HELP_COMMANDS`
  through a call-time import of `repl` (the banner is derived from the merged
  registry, so packs cannot import it at module load — same late-binding the
  file already used internally).
- **Shared mini-helpers move down**: `parse_int_arg` and `format_time` move to
  `commands.py` (the dependency-free mechanism module); `repl._parse_int_arg`
  / `repl._format_time` stay as aliases (tests call `repl._parse_int_arg`).
- **Re-exports preserve the entire test surface**: every moved name that any
  test imports from `ubongo.repl` or reaches as `repl.X` (the
  `_parse_*`/`_render_*`/`_cmd_*`/sentinel inventory was enumerated up front)
  is re-imported into repl's namespace with a `# moved in candidate 18` note.
  Zero test edits.

## Deviation from the roadmap (recorded)

The roadmap sketched a fourth "core/governance pack" (`/queue`, `/decisions`,
`/policy`, `/agents`, `/trace`, `/exec`, `/profile`, `/mode`, `/skill`,
`/skills`, `/summary`, `/reload`). Examined: these are the REPL's own
control/diagnostic surface over many modules — `/summary` is additionally
pinned by the `ubongo.repl.complete` patch target — and a grab-bag module has
no better locality than repl itself. They stay. repl.py lands ~700 lines, not
the roadmap's aspirational ~600; the subsystem-locality goal (an authoring
change edits `authoring/`) is fully met.

## Done when

- The three packs exist; repl.py assembles fragments; banner byte-identical;
  `pytest -q` green with zero test edits; smoke gate green.
