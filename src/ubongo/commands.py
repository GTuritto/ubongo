"""The slash-command registry seam behind the REPL.

A **slash command** is a REPL control/diagnostic input (`/trace`, `/mode`,
`/evolution`, …) — distinct from a turn, which goes through the Master pipeline.
This module owns the registry *mechanism*: the command record, the dispatch, and
a help banner derived from the registry. The REPL (`repl.py`) registers the
concrete commands and supplies the handlers; the loop dispatches over the seam
instead of an 18-branch ``if head == …`` chain, so adding a command is a registry
entry, not an edit to the loop.

Handlers are pure: they take the raw command line plus the mutable
:class:`ReplState` and return the text to emit (or ``None``). The loop owns
output (routing it through the notification queue) — so handlers carry no I/O and
are testable on their own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class ReplState:
    """The mutable turn state the REPL loop threads across commands and turns."""

    persona: str
    auto_mode: bool
    pending_skill: str | None
    pending_workflow: str | None
    keep_going: bool = True
    # Candidate 10: when True, each turn's master.handle runs under cProfile
    # (/profile cpu on|off). Defaulted so existing constructions stay valid.
    cpu_profile: bool = False


# A handler takes the full (stripped) command line and the REPL state, may mutate
# the state (e.g. /mode sets pending_workflow), and returns text to emit or None.
Handler = Callable[[str, ReplState], "str | None"]


@dataclass(frozen=True)
class Command:
    handler: Handler
    usage: str  # canonical usage including the slash token, e.g. "/queue [N]"


class _Unknown:
    """Sentinel returned by :func:`dispatch` when the head isn't registered."""

    __slots__ = ()


UNKNOWN = _Unknown()


def split_command(stripped: str) -> tuple[str, str]:
    """``"/trace 3"`` -> ``("trace", "3")``. Leading slashes stripped, head lowercased."""
    body = stripped.lstrip("/")
    if not body:
        return "", ""
    parts = body.split(maxsplit=1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def dispatch(
    registry: dict[str, Command], head: str, line: str, state: ReplState
) -> "str | None | _Unknown":
    """Look up `head` and run its handler with the full command `line`.

    Returns the handler's output (str or None), or :data:`UNKNOWN` when `head`
    is not in the registry (the caller falls back to persona/exit handling)."""
    cmd = registry.get(head)
    if cmd is None:
        return UNKNOWN
    return cmd.handler(line, state)


def help_banner(registry: dict[str, Command], *, extra: tuple[str, ...] = ()) -> str:
    """Build the help line from the registry — the single source of truth for
    which commands exist. `extra` appends tokens for commands handled outside the
    registry (persona switches / exit)."""
    usages = [registry[name].usage for name in registry]
    return "Try " + ", ".join([*usages, *extra]) + "."


def parse_int_arg(line: str, command: str, default: int) -> "int | None":
    """Shared parser for the `/<command> [N]` shape: returns N (default when
    omitted), or None for a malformed/non-positive arg. (Moved from repl.py in
    candidate 18 so command packs can use it without importing the REPL.)"""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != command:
        return None
    if len(parts) == 1:
        return default
    try:
        n = int(parts[1].strip())
    except ValueError:
        return None
    return n if n > 0 else None


def format_time(ts: "str | None") -> str:
    """`2026-05-12T15:51:57.123Z` -> `15:51:57`; em-dash when absent."""
    if ts is None:
        return "—"
    return ts[11:19] if len(ts) >= 19 else ts
