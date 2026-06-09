from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("ubongo.skills")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SKILLS_DIR = _REPO_ROOT / "config" / "skills"

RISK_VOCAB = {"low", "medium", "high", "destructive"}
REVERSIBILITY_VOCAB = {"reversible", "irreversible"}
PERSONA_VOCAB = {"architect", "operator", "casual"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    risk: str
    reversibility: str
    default_persona: str | None
    prompts: dict[str, str] = field(default_factory=dict)
    dir: Path = field(default_factory=Path)


_skills_dir: Path = _DEFAULT_SKILLS_DIR
_registry: dict[str, Skill] | None = None
_body_cache: dict[str, str] = {}
_prompt_cache: dict[tuple[str, str], str] = {}


def set_skills_dir(path: Path | None) -> None:
    """Override the skills directory (test hook). Pass None to reset to default."""
    global _skills_dir
    _skills_dir = Path(path) if path is not None else _DEFAULT_SKILLS_DIR
    reload()


def skills_dir() -> Path:
    """The live skills directory currently in effect (honors set_skills_dir). The
    authoring approval gate materializes an approved candidate into here."""
    return _skills_dir


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + len("\n---\n"):].lstrip("\n")
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        raise ValueError(f"Skill frontmatter must be a YAML mapping, got {type(fm).__name__}")
    return fm, body


def _parse_skill(skill_dir: Path) -> Skill:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")
    fm, _ = _split_frontmatter(skill_md.read_text(encoding="utf-8"))

    name = fm.get("name")
    description = fm.get("description")
    risk = fm.get("risk")
    reversibility = fm.get("reversibility")
    default_persona = fm.get("default_persona")
    prompts = fm.get("prompts") or {}

    if not isinstance(name, str) or not name:
        raise ValueError(f"Skill {skill_dir.name} frontmatter missing or invalid 'name'")
    if not isinstance(description, str) or not description:
        raise ValueError(f"Skill {name} frontmatter missing or invalid 'description'")
    if risk not in RISK_VOCAB:
        raise ValueError(f"Skill {name} 'risk' must be one of {sorted(RISK_VOCAB)}, got {risk!r}")
    if reversibility not in REVERSIBILITY_VOCAB:
        raise ValueError(
            f"Skill {name} 'reversibility' must be one of {sorted(REVERSIBILITY_VOCAB)}, got {reversibility!r}"
        )
    if default_persona is not None and default_persona not in PERSONA_VOCAB:
        raise ValueError(
            f"Skill {name} 'default_persona' must be one of {sorted(PERSONA_VOCAB)} or null, got {default_persona!r}"
        )
    if not isinstance(prompts, dict):
        raise ValueError(f"Skill {name} 'prompts' must be a mapping, got {type(prompts).__name__}")
    for key, rel in prompts.items():
        if not isinstance(key, str) or not isinstance(rel, str):
            raise ValueError(f"Skill {name} 'prompts' entries must be str -> str, got {key!r} -> {rel!r}")

    if name != skill_dir.name:
        logger.warning(
            "skill_name_dir_mismatch",
            extra={"skill_dir_name": skill_dir.name, "frontmatter_name": name},
        )

    return Skill(
        name=name,
        description=description,
        risk=risk,
        reversibility=reversibility,
        default_persona=default_persona,
        prompts=dict(prompts),
        dir=skill_dir,
    )


def _discover() -> dict[str, Skill]:
    registry: dict[str, Skill] = {}
    if not _skills_dir.exists():
        return registry
    for entry in sorted(_skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").exists():
            continue
        skill = _parse_skill(entry)
        registry[skill.name] = skill
    return registry


def _ensure() -> dict[str, Skill]:
    global _registry
    if _registry is None:
        _registry = _discover()
    return _registry


def list_skills() -> list[Skill]:
    return sorted(_ensure().values(), key=lambda s: s.name)


def get(name: str) -> Skill:
    registry = _ensure()
    if name not in registry:
        raise KeyError(f"Unknown skill: {name}")
    return registry[name]


def has(name: str) -> bool:
    return name in _ensure()


def body(name: str) -> str:
    cached = _body_cache.get(name)
    if cached is not None:
        return cached
    skill = get(name)
    text = (skill.dir / "SKILL.md").read_text(encoding="utf-8")
    _, raw_body = _split_frontmatter(text)
    stripped = raw_body.rstrip()
    _body_cache[name] = stripped
    logger.info("skill_body_loaded", extra={"skill_name": name})
    return stripped


def prompt(name: str, key: str) -> str:
    cached = _prompt_cache.get((name, key))
    if cached is not None:
        return cached
    skill = get(name)
    if key not in skill.prompts:
        raise KeyError(f"Skill {name} has no prompt named {key!r}")
    rel = skill.prompts[key]
    # Confine the prompt path to skill.dir. A malicious or compromised SKILL.md
    # could declare prompts: { x: ../../../.env } and exfiltrate secrets into
    # the LLM prompt; reject anything that escapes the skill root or is an
    # absolute path.
    from pathlib import PurePath

    if PurePath(rel).is_absolute():
        raise ValueError(f"Skill {name} prompt {key!r} path must be relative")
    skill_root = skill.dir.resolve()
    candidate = (skill.dir / rel).resolve()
    try:
        candidate.relative_to(skill_root)
    except ValueError:
        raise ValueError(
            f"Skill {name} prompt {key!r} escapes skill directory: {rel!r}"
        ) from None
    if not candidate.exists():
        raise FileNotFoundError(f"Skill {name} prompt {key!r} not found at {candidate}")
    text = candidate.read_text(encoding="utf-8")
    _prompt_cache[(name, key)] = text
    logger.info("skill_prompt_loaded", extra={"skill_name": name, "prompt_key": key})
    return text


def resolve(*, pinned: str | None, suggested: str | None) -> Skill | None:
    """Resolve which skill applies to a turn. Pinned beats suggested; unknowns fall through."""
    for candidate in (pinned, suggested):
        if not candidate:
            continue
        if has(candidate):
            return get(candidate)
        logger.warning("skill_resolve_unknown", extra={"skill_name": candidate})
    return None


def reload() -> None:
    global _registry
    _registry = None
    _body_cache.clear()
    _prompt_cache.clear()
