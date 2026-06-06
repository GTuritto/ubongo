# Plan — Candidate A (04): A command registry behind the REPL

Architecture-review deepening candidate **A / 04** (Strong, top recommendation) from the
2026-06-06 review (the carried-over candidate 04, now broader). Branch:
`improve/04-command-registry` (off `main`, which has 01/02/03 + sandbox fix).

**Approved scope (broadest):** registry + parser dedup + relocate evolution logic + route
command output through `notification_queue` (honour ADR-0002). Four parts, one PR.

## Problem

- `repl._repl_loop` (`repl.py:936–1120`) dispatches ~18 slash commands as inline
  `if head == "…"` branches, plus a second path (`handle_slash`) for persona/auto/exit. The
  seam to add a command is "edit `_repl_loop` in place." No registry.
- Three near-identical int-arg parsers (`_parse_queue_command`, `_parse_decisions_command`,
  `_parse_trace_command`, `repl.py:29–71`) — "optional int, default N".
- `_render_optimize` / `_render_evaluate` carry **evolution business logic** (generate +
  persist variants, evaluate a cohort) — that belongs in `ubongo.evolution`, not the REPL.
- Only the model reply crosses the `notification_queue` seam; all 18 command outputs `print()`
  straight to stdout (ADR-0002 says "every outbound message passes through `notification_queue`").

## Solution — four parts

### 1. Command registry (new `src/ubongo/commands.py`)

A `name → Command` registry; the loop dispatches. Each command is an adapter owning its own
parse + render; persona/auto/exit fold in as registry entries too (one dispatch path, not two).

```python
@dataclass
class ReplState:                 # the mutable turn state the loop threads today
    persona: str
    auto_mode: bool
    pending_skill: str | None
    pending_workflow: str | None
    keep_going: bool = True       # /exit sets False

# A handler takes the post-name argument string + the REPL state, mutates state as
# needed (e.g. /mode sets pending_workflow, /casual sets persona), and returns the
# text to emit (or None for no output).
Handler = Callable[[str, ReplState], str | None]

@dataclass(frozen=True)
class Command:
    handler: Handler
    usage: str          # one-line usage; the help banner is derived from the registry

COMMANDS: dict[str, Command] = { "trace": Command(...), "evolution": Command(...), ... }

def dispatch(head: str, rest: str, state: ReplState) -> str | None | _UNKNOWN
def help_banner() -> str   # built from COMMANDS — replaces the hand-maintained _HELP_COMMANDS
```

The `_parse_*` / `_render_*` helpers move into `commands.py` beside their handlers (locality:
a command's parse + render + dispatch in one place). `repl.py` keeps the loop, the model-reply
path, the interactive prompts, and the new `emit()`. The loop body becomes:

```python
head, rest = split(stripped)
out = commands.dispatch(head, rest, state)
if out is _UNKNOWN: emit(commands.help_banner_for_unknown(head))
elif out is not None: emit(out)
if not state.keep_going: return 0
```

### 2. Parser dedup

One `_parse_int_arg(rest: str, *, default: int) -> int | None` replaces the three. (`None`
signals a malformed arg → the command's `usage` is emitted.)

### 3. Relocate evolution logic

Move the generate+persist / evaluate-cohort bodies out of `_render_optimize` /
`_render_evaluate` into `ubongo.evolution` (e.g. `evolution.optimize_target(target)` and
`evolution.evaluate_target_cohort(target)` returning structured results). The `optimize` /
`evaluate` command handlers call those, then format. The REPL stops owning GP business logic.

### 4. Route command output through `notification_queue` (honour ADR-0002)

A `repl.emit(text)` mirroring the reply path: `tok = queue.enqueue_for_delivery(text,
source="command", after_send_payload=None); print(text); queue.flush_delivered(tok)`. So
command output crosses the one outbound seam (auditable; `before_send`/`after_send` fire; v0.2
transports inherit it), with `after_send_payload=None` so it does **not** trigger vault
projection (only assistant turns project — single-writer intact).

**Consequence + decision:** command rows would now appear in `notification_queue`, so `/queue`
(and the Phase-7 queue tests) would change. Fix: `/queue` filters to the assistant-turn
sources (`response`, `error`, `rejected`), excluding `command`, so its meaning is preserved.
`source` gains the value `"command"`.

**Interactive prompts stay direct.** The approval `y/n/why` prompt and the repair-retry `y/n`
prompt are synchronous request/response I/O on the turn path, not deliverable notifications —
they remain direct `input()`/`print()`, not queued. This is the one carve-out (see ADR note).

## Side effects (per the architecture-review skill)

- **CONTEXT.md** — add a **Slash command** / **Command registry** term under a new "CLI" group:
  a Slash command is a REPL control/diagnostic input (`/trace`, `/mode`, …) dispatched via the
  Command registry seam; distinct from a turn (which goes through the Master pipeline).
- **ADR-0002** — annotate (not reverse): "every outbound message passes through
  `notification_queue`" now holds for assistant turns **and** command output (source
  `command`); record the carve-out that interactive prompts are synchronous I/O, exempt. This
  closes the rule-vs-practice gap the review flagged as candidate C, in the honour direction.

## Behavior to preserve (guarded by tests)

- Every command's output text is byte-identical; the help banner lists the same commands
  (now derived from the registry).
- `/queue` shows the same rows as before (filtered to turn sources), despite command output now
  being queued.
- persona/auto/exit (`handle_slash`) behavior unchanged, now via registry entries.
- `/mode`, `/skill` one-shot pending state still set + cleared per turn.
- evolution `/optimize` `/evaluate` produce identical output; the logic just lives in
  `ubongo.evolution` now.

## Tests

- `tests/test_repl.py`, `tests/test_repl_trace.py`, and the Phase-7 `/queue` tests stay green
  (behavior preserved); `/queue` test may need the source-filter assertion updated.
- New `tests/test_commands.py`: registry dispatch (known/unknown), `help_banner` derived from
  the registry, `_parse_int_arg`, a state-mutating handler (`/mode` sets `pending_workflow`),
  and `emit` routing a command output row with `source="command"` that `/queue` filters out.
- New evolution-function tests for the relocated `optimize_target` / `evaluate_target_cohort`.
- Full suite green, then live smoke of the `/` commands + a turn.

## Risks / ADR check

- **ADR-0002** — honoured, with the documented interactive-prompt carve-out (annotate, don't reverse).
- **ADR-0003** — untouched (this is the CLI surface, not the Master pipeline / modes).
- **Blast radius** — `repl.py` (large internal move), new `commands.py`, `evolution` (two new
  functions), `delivery`/`/queue` filter, ADR-0002 + CONTEXT.md. Mitigated by behavior
  preservation held by the existing REPL/queue tests + new unit tests + live smoke.
- This is the broadest of the three remaining candidates by choice; it also resolves candidate
  C (the outbound seam) in passing.

## Out of scope

Candidates B (master result cascade), D (trace store split), E (fan-out wrapper), F (evolution
aggregation). Building a second (Telegram) transport — this only makes the seam ready.

## Done when

- Slash commands dispatch via the `commands.COMMANDS` registry; adding a command = a handler +
  a registry entry, no `_repl_loop` edit. Help banner derived from the registry. One int-arg
  parser. `/optimize` `/evaluate` logic lives in `ubongo.evolution`. Command output flows
  through `notification_queue` (source `command`); `/queue` filtered to turn sources; prompts
  stay direct. ADR-0002 annotated, CONTEXT.md term added.
- Existing REPL/queue/trace tests green, new `test_commands.py` + evolution tests green, full
  suite green, live `/`-command smoke passes.
- Draft PR opened against `main`, marked ready once the above hold.
