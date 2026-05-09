from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _REPO_ROOT / "config"

_cache: dict[Path, str] = {}


def _read_cached(path: Path) -> str:
    cached = _cache.get(path)
    if cached is not None:
        return cached
    if not path.exists():
        raise FileNotFoundError(f"Context file not found: {path}")
    text = path.read_text(encoding="utf-8")
    _cache[path] = text
    return text


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n"):].lstrip("\n")


def build_system_prompt(
    persona: str,
    skill: str | None = None,
    agent_role: str | None = None,
) -> str:
    sections: list[str] = []

    ubongo_md = _read_cached(_CONFIG_DIR / "UBONGO.md")
    sections.append(ubongo_md.rstrip())

    persona_path = _CONFIG_DIR / "personas" / f"{persona}.md"
    persona_body = _strip_frontmatter(_read_cached(persona_path))
    sections.append(persona_body.rstrip())

    if skill is not None:
        skill_path = _CONFIG_DIR / "skills" / skill / "SKILL.md"
        skill_body = _strip_frontmatter(_read_cached(skill_path))
        sections.append(f"## Active Skill: {skill}\n\n{skill_body.rstrip()}")

    if agent_role is not None:
        sections.append(f"## Agent Role: {agent_role}")

    return "\n\n".join(sections)


def reload() -> None:
    _cache.clear()
