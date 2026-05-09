from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    pass


_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SETTINGS_PATH = _REPO_ROOT / "config" / "settings.yaml"

_cache: dict[str, Any] | None = None
_dotenv_loaded = False


def _ensure_dotenv() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    load_dotenv(_REPO_ROOT / ".env")
    _dotenv_loaded = True


def _resolve_env_refs(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            var = match.group(1)
            resolved = os.environ.get(var)
            if resolved is None:
                raise ConfigError(f"Environment variable {var} referenced in settings.yaml is not set")
            return resolved
        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(v) for v in value]
    return value


def _validate_required(cfg: dict[str, Any]) -> None:
    api_keys = cfg.get("api_keys", {})
    openrouter = api_keys.get("openrouter", {})
    env_var = openrouter.get("env")
    if not env_var:
        raise ConfigError("settings.yaml: api_keys.openrouter.env is missing")
    if not os.environ.get(env_var):
        raise ConfigError(f"{env_var} not set. Copy .env.example to .env and fill it in.")


def load_config(path: Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is not None and not force_reload:
        return _cache

    _ensure_dotenv()
    settings_path = path or _DEFAULT_SETTINGS_PATH
    if not settings_path.exists():
        raise ConfigError(f"settings.yaml not found at {settings_path}")

    with settings_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = _resolve_env_refs(raw)
    _validate_required(cfg)
    _cache = cfg
    return cfg


def reload() -> None:
    global _cache
    _cache = None
