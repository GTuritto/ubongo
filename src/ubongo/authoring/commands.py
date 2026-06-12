"""The authoring command pack (candidate 18).

Slash-command handlers, parsers, and renderers for the authoring subsystem,
moved out of repl.py so a authoring change edits this package. Registered via
the COMMANDS fragment below, which repl.py merges into its registry; handler
contract per ubongo.commands (pure: line + ReplState -> text). The help banner
is derived from the merged registry, so packs resolve it late via _help().
"""

from __future__ import annotations

import logging

from ubongo import skills
from ubongo.commands import Command, ReplState
from ubongo.commands import format_time as _format_time  # noqa: F401
from ubongo.evolution.commands import _diff_preview  # shared unified-diff preview
from ubongo.memory import authoring_state
from ubongo.memory import store

logger = logging.getLogger("ubongo.authoring.commands")


def _help() -> str:
    from ubongo import repl
    return repl._HELP_COMMANDS


def _parse_author_command(line: str) -> str | None:
    """`/author <description>` -> the description, or None if missing."""
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if not parts or parts[0].lower() != "author":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return None
    return parts[1].strip()

def _render_author(description: str) -> str:
    from ubongo.authoring import manual

    try:
        outcome = manual.author_skill(description)
    except manual.AuthoringError as exc:
        return f"Could not author a skill: {exc}"
    c = outcome.candidate
    kind = "command skill" if c.is_command_skill else "prompt skill"
    lines = [
        f"Drafted candidate #{outcome.candidate_id} '{c.name}' (gen {outcome.generation}, {kind}).",
        f"  risk: {c.risk}   reversibility: {c.reversibility}"
        + (f"   persona: {c.default_persona}" if c.default_persona else ""),
        f"  {c.description}",
    ]
    if c.is_command_skill:
        lines.append(f"  command: {c.command_template.strip()}")
    if outcome.quality is not None:
        lines.append(f"  quality: {outcome.quality:.3f} (estimated)")
    lines.append("  status: quarantined (not discoverable until approved).")
    lines.append("  Review with /skill-candidates.")
    return "\n".join(lines)

def _cmd_author(line: str, state: ReplState) -> str | None:
    description = _parse_author_command(line)
    if not description:
        return f"Usage: /author <capability description>. {_help()}"
    return _render_author(description)

_AUTHORING_SUBCOMMANDS = ("status", "pause", "resume", "off")

def _parse_authoring_command(line: str) -> str | None:
    raw = line.strip().lstrip("/")
    parts = raw.split(maxsplit=1)
    if not parts or parts[0].lower() != "authoring":
        return None
    if len(parts) == 1 or not parts[1].strip():
        return "status"
    return parts[1].strip().split()[0].lower()

def _render_authoring_status() -> str:
    from ubongo.config import load_authoring

    status = authoring_state.get_authoring_status()
    cap = int((load_authoring() or {}).get("max_calls_per_hour", 20))
    spent = authoring_state.authoring_calls_in_last_hour()
    drafts = authoring_state.authored_skills(status="draft", limit=100)
    auto = [d for d in drafts if d["source"] == "auto"]
    lines = [
        f"Authoring daemon: {status}  (budget {spent}/{cap} calls in the last hour)",
        f"  pending drafts: {len(drafts)} ({len(auto)} auto-authored) — review with /skill-candidates",
    ]
    runs = authoring_state.authoring_runs_recent(5)
    if runs:
        lines.append("  recent cycles:")
        for r in runs:
            lines.append(
                f"    #{r['id']} {r['outcome']:<11} gap={r['gap'] or '-'} "
                f"cand={r['candidate_id'] or '-'} calls={r['calls_spent']}"
            )
    return "\n".join(lines)

def _render_authoring_control(sub: str) -> str:
    if sub == "pause":
        authoring_state.set_authoring_status("paused")
        return "Authoring daemon paused."
    if sub == "resume":
        authoring_state.set_authoring_status("running")
        return ("Authoring daemon running. It drafts candidates into quarantine on "
                "recurring capability gaps; approval stays manual (/skill-candidates).")
    authoring_state.set_authoring_status("off")
    return "Authoring daemon off."

def _cmd_authoring(line: str, state: ReplState) -> str | None:
    sub = _parse_authoring_command(line)
    if sub is None or sub == "status":
        return _render_authoring_status()
    if sub in ("pause", "resume", "off"):
        return _render_authoring_control(sub)
    return f"Unknown subcommand: {sub}. Usage: /authoring status|pause|resume|off."

def _render_skill_candidates_list() -> str:
    rows = authoring_state.authored_skills(limit=30)
    if not rows:
        return "No authored skill candidates yet. Draft one with /author <description>."
    lines = ["Authored skill candidates (newest first):"]
    for r in rows:
        cand = r.get("candidate") or {}
        is_cmd = bool((cand.get("command_template") or "").strip())
        quality = r.get("quality")
        q = f" quality={quality:.3f}" if isinstance(quality, (int, float)) else ""
        lines.append(
            f"  #{r['id']} {r['name']:<24} {r['status']:<11} "
            f"gen={r['generation']} {'cmd' if is_cmd else 'prompt'} "
            f"src={r['source']}{q}"
        )
        if r["status"] == "draft":
            lines.extend("      " + dl for dl in _candidate_collision_diff(r))
    lines.append(
        "Approve with /skill-candidates approve <id> (reject <id>, rollback <name>)."
    )
    return "\n".join(lines)

def _candidate_collision_diff(row: dict) -> list[str]:
    """If a draft would overwrite a live skill of the same name, a compact diff
    of the live SKILL.md -> the candidate's, so the reviewer sees the change
    before approving. Empty for a fresh (non-colliding) candidate."""
    from pathlib import Path

    live = skills.skills_dir() / row["name"] / "SKILL.md"
    qpath = row.get("quarantine_path")
    if not live.exists() or not qpath:
        return []
    qmd = Path(qpath) / "SKILL.md"
    if not qmd.exists():
        return []
    try:
        diff = _diff_preview(live.read_text(encoding="utf-8"), qmd.read_text(encoding="utf-8"))
    except OSError:
        return []
    header = f"(would overwrite live '{row['name']}'{'' if diff else '; no textual change'}:)"
    return [header] + diff

def _parse_skill_candidates_command(line: str):
    """Parse `/skill-candidates [approve <id> | reject <id> | rollback <name>]`.
    Returns ("list", None), ("approve"|"reject", id:int), ("rollback", name),
    ("usage", None), or None for other commands."""
    raw = line.strip().lstrip("/")
    parts = raw.split()
    if not parts or parts[0].lower() != "skill-candidates":
        return None
    if len(parts) == 1 or parts[1].lower() == "list":
        return ("list", None)
    sub = parts[1].lower()
    if sub in ("approve", "reject"):
        if len(parts) < 3:
            return ("usage", None)
        try:
            return (sub, int(parts[2]))
        except ValueError:
            return ("usage", None)
    if sub == "rollback":
        if len(parts) < 3:
            return ("usage", None)
        return ("rollback", parts[2])
    return ("usage", None)

def _render_skill_candidates_action(action: str, arg) -> str:
    from ubongo.authoring import promotion

    try:
        if action == "approve":
            r = promotion.approve(arg)
            msg = f"Approved #{r.candidate_id} '{r.name}' — registered and now in /skills."
            if r.backed_up:
                msg += f"\n  Prior version backed up to {r.backup_path}."
            return msg
        if action == "reject":
            r = promotion.reject(arg)
            return f"Rejected #{r.candidate_id} '{r.name}'. Left in quarantine."
        if action == "rollback":
            r = promotion.rollback(arg)
            if r.restored:
                return f"Rolled back '{r.name}' — restored the prior version from {r.backup_path}."
            return f"Rolled back '{r.name}' — unregistered (no prior version to restore)."
    except promotion.PromotionError as exc:
        return f"Cannot do that: {exc}"
    return "Unknown action."

def _cmd_skill_candidates(line: str, state: ReplState) -> str | None:
    parsed = _parse_skill_candidates_command(line)
    if parsed is None or parsed[0] == "list":
        return _render_skill_candidates_list()
    if parsed[0] == "usage":
        return "Usage: /skill-candidates [approve <id> | reject <id> | rollback <name>]."
    return _render_skill_candidates_action(parsed[0], parsed[1])

# The registry fragment repl.py merges (order preserved by the assembler).
COMMANDS: dict[str, Command] = {
    "author": Command(_cmd_author, "/author <description>"),
    "authoring": Command(_cmd_authoring, "/authoring <status|pause|resume|off>"),
    "skill-candidates": Command(_cmd_skill_candidates, "/skill-candidates [approve <id>|reject <id>|rollback <name>]"),
}
