from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ubongo.config import load_config

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _REPO_ROOT / "config" / "personas"


@dataclass(frozen=True)
class Persona:
    name: str
    body: str
    model: str
    max_tokens: int


_registry: dict[str, Persona] = {}


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
        raise ValueError(f"Persona frontmatter must be a YAML mapping, got {type(fm).__name__}")
    return fm, body


def _load(name: str) -> Persona:
    path = _PERSONAS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Persona file not found: {path}")
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    model_key = fm.get("default_model")
    max_tokens = fm.get("max_tokens")
    if not model_key:
        raise ValueError(f"Persona '{name}' frontmatter missing 'default_model'")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError(f"Persona '{name}' frontmatter missing or invalid 'max_tokens'")

    config = load_config()
    models = config.get("models", {})
    if model_key not in models:
        raise ValueError(
            f"Persona '{name}' references models.{model_key}, "
            f"but settings.yaml has no such entry"
        )
    return Persona(name=name, body=body.rstrip(), model=models[model_key], max_tokens=max_tokens)


def get(name: str) -> Persona:
    cached = _registry.get(name)
    if cached is not None:
        return cached
    persona = _load(name)
    _registry[name] = persona
    return persona


def reload() -> None:
    _registry.clear()
