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
_DEFAULT_GOVERNANCE_PATH = _REPO_ROOT / "config" / "governance.yaml"

_cache: dict[Path, dict[str, Any]] = {}
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
    """Load settings.yaml, with a per-path cache.

    The earlier single-slot cache returned the first-loaded config regardless
    of the `path` argument on subsequent calls (review finding #5). Tests and
    tools that ask for a different config file silently got stale data — a
    real risk for security/feature toggles. Now keyed by resolved path.
    """
    _ensure_dotenv()
    settings_path = (path or _DEFAULT_SETTINGS_PATH).resolve()
    if not force_reload and settings_path in _cache:
        return _cache[settings_path]

    if not settings_path.exists():
        raise ConfigError(f"settings.yaml not found at {settings_path}")

    with settings_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = _resolve_env_refs(raw)
    _validate_required(cfg)
    _cache[settings_path] = cfg
    return cfg


def load_governance(path: Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    """Load governance.yaml — the decision-matrix rules (Phase 14).

    Mirrors `load_config()`: per-path cache, env-ref resolution. Unlike
    settings.yaml there is no required-field validation — a missing file is a
    hard error (governance must be explicit), but the body is plain data.
    """
    _ensure_dotenv()
    gov_path = (path or _DEFAULT_GOVERNANCE_PATH).resolve()
    if not force_reload and gov_path in _cache:
        return _cache[gov_path]

    if not gov_path.exists():
        raise ConfigError(f"governance.yaml not found at {gov_path}")

    with gov_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = _resolve_env_refs(raw)
    _cache[gov_path] = cfg
    return cfg


def reload() -> None:
    _cache.clear()
