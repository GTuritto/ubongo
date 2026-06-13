"""The memory command pack (candidate 18).

Slash-command handlers, parsers, and renderers for the memory subsystem,
moved out of repl.py so a memory change edits this package. Registered via
the COMMANDS fragment below, which repl.py merges into its registry; handler
contract per ubongo.commands (pure: line + ReplState -> text). The help banner
is derived from the merged registry, so packs resolve it late via _help().
"""

from __future__ import annotations

import logging

from ubongo.commands import Command, ReplState
from ubongo.commands import format_time as _format_time  # noqa: F401
from ubongo.memory import index_state
from ubongo.memory import store, vault

logger = logging.getLogger("ubongo.memory.commands")


def _help() -> str:
    from ubongo import repl
    return repl._HELP_COMMANDS


def _parse_recall_command(line: str) -> str | None:
    """Returns the query from `/recall [query]` ("" for no query), or None for
    other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if parts[0].lower() != "recall":
        return None
    return parts[1].strip() if len(parts) > 1 else ""

def _render_recall(query: str) -> str:
    """Phase 20f: show what recall would surface for the current conversation —
    the recency window, semantic hits (when embeddings are on), and the vault
    graph neighbors of today's daily note. A direct read tool (no master.handle)."""
    from datetime import datetime, timezone

    from ubongo.memory import embeddings, graph, store, vault

    session = store.get_session()
    conv_id = session.current_conversation_id if session else None
    if conv_id is None:
        return "No conversation yet."

    # default query = the latest user message
    if not query:
        recent_user = [m for m in store.last_n_messages(conv_id, 20) if m.role == "user"]
        query = recent_user[-1].content if recent_user else ""

    ctx = store.recall(conv_id, query=query or None)
    lines = [f"Recall for conversation {conv_id}" + (f' — query: "{query}"' if query else "")]

    if ctx.summary_text:
        lines.append(f"\nsummary: {ctx.summary_text[:200]}")

    lines.append(f"\nrecency window (last {len(ctx.messages)}):")
    for m in ctx.messages[-6:]:
        lines.append(f"  {m.role}: {' '.join(m.content.split())[:80]}")

    if not embeddings.enabled():
        lines.append("\nsemantic: (embeddings disabled — recency only)")
    elif not embeddings.vec_available():
        lines.append("\nsemantic: (sqlite-vec unavailable — recency only)")
    elif ctx.semantic_messages:
        lines.append("\nsemantic hits (outside the recency window):")
        for m in ctx.semantic_messages:
            lines.append(f"  #{m.id} {m.role}: {' '.join(m.content.split())[:80]}")
    else:
        lines.append("\nsemantic: (no hits)")

    today = datetime.now(timezone.utc).date().isoformat()
    note = f"{vault._daily_subdir()}/{today}.md"
    nbrs = graph.neighbors(note)
    lines.append(f"\nvault graph — neighbors of {note}: " + (", ".join(nbrs) if nbrs else "(none)"))
    return "\n".join(lines)

_AUDIT_CATEGORIES = ("governance", "evolution", "sync", "authoring", "mcp")

def _parse_audit_command(line: str):
    """Parse `/audit [category] [N]`. Returns (category|None, n) or None for
    other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "audit":
        return None
    category, n = None, 20
    for tok in parts[1:]:
        if tok.lower() in _AUDIT_CATEGORIES:
            category = tok.lower()
        else:
            try:
                n = int(tok)
            except ValueError:
                pass
    return (category, n)

def _render_audit(category, n: int) -> str:
    """Phase 21d: tail the unified audit log, optionally filtered by category."""
    from ubongo.memory import vault

    rows = vault.audit_tail(category, n)
    if not rows:
        return f"No audit entries{f' for {category}' if category else ''}."
    header = f"Audit log (last {len(rows)}{f', {category}' if category else ''}):"
    return header + "\n" + "\n".join(f"  {r[2:]}" for r in rows)

def _parse_conflicts_command(line: str):
    """Parse `/conflicts` (list) or `/conflicts resolve <id> <keep-mine|keep-theirs|merge>`.
    Returns ("list", None, None), ("resolve", id, resolution), ("usage", None, None),
    or None for other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "conflicts":
        return None
    if len(parts) == 1:
        return ("list", None, None)
    if parts[1].lower() == "resolve" and len(parts) >= 4:
        try:
            cid = int(parts[2])
        except ValueError:
            return ("usage", None, None)
        res = parts[3].lower()
        if res not in ("keep-mine", "keep-theirs", "merge"):
            return ("usage", None, None)
        return ("resolve", cid, res)
    return ("usage", None, None)

def _render_conflicts_list() -> str:
    from ubongo.memory import store

    rows = index_state.open_vault_conflicts()
    if not rows:
        return "No open vault conflicts."
    lines = [f"Open vault conflicts ({len(rows)}):"]
    for r in rows:
        lines.append(f"  #{r['id']}  {r['path']}  (edited externally at {r['detected_at']})")
    lines.append("Resolve with /conflicts resolve <id> <keep-mine|keep-theirs|merge>.")
    return "\n".join(lines)

def _render_conflicts_resolve(cid: int, resolution: str) -> str:
    from ubongo.memory import store, vault

    conflict = index_state.get_vault_conflict(cid)
    if conflict is None or conflict["status"] != "open":
        return f"No open conflict #{cid}."
    ok = index_state.resolve_vault_conflict(cid, resolution)
    if not ok:
        return f"No open conflict #{cid}."
    vault.append_audit_entry("sync", f"resolved conflict #{cid} on {conflict['path']} -> {resolution}")
    note = ""
    if resolution == "keep-mine":
        note = " (note: daily notes are append-only; the system keeps appending and does not snapshot, so the on-disk edit remains)"
    return f"Conflict #{cid} resolved: {resolution}{note}."

def _cmd_recall(line: str, state: ReplState) -> str | None:
    q = _parse_recall_command(line)
    return _render_recall(q or "")

def _cmd_audit(line: str, state: ReplState) -> str | None:
    parsed = _parse_audit_command(line)
    cat, n = parsed if parsed else (None, 20)
    return _render_audit(cat, n)

def _cmd_conflicts(line: str, state: ReplState) -> str | None:
    parsed = _parse_conflicts_command(line)
    if parsed is None or parsed[0] == "list":
        return _render_conflicts_list()
    if parsed[0] == "usage":
        return "Usage: /conflicts [resolve <id> <keep-mine|keep-theirs|merge>]."
    return _render_conflicts_resolve(parsed[1], parsed[2])

# The registry fragment repl.py merges (order preserved by the assembler).
COMMANDS: dict[str, Command] = {
    "recall": Command(_cmd_recall, "/recall [query]"),
    "audit": Command(_cmd_audit, "/audit [category]"),
    "conflicts": Command(_cmd_conflicts, "/conflicts"),
}
