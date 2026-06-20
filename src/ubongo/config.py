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
_DEFAULT_JOBS_PATH = _REPO_ROOT / "config" / "jobs.yaml"

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


def load_evolution(path: Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    """Return the `evolution:` block from settings.yaml (Phase 16).

    A thin accessor over `load_config()` so the GP layer reads its knobs
    (`population_size`, generator model) through one named entry point. Returns
    an empty dict if the block is absent, so callers can fall back to defaults.
    """
    return load_config(path, force_reload=force_reload).get("evolution", {})


def load_authoring(path: Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    """Return the `authoring:` block from settings.yaml (self-extension experiment).

    A thin accessor over `load_config()`, mirroring `load_evolution()`, so the
    skill-authoring layer reads its knobs (drafting model, daemon budget/cron)
    through one named entry point. Empty dict if the block is absent.
    """
    return load_config(path, force_reload=force_reload).get("authoring", {})


def load_jobs(path: Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    """Return the `jobs:` block from settings.yaml (v0.5 phase 06) — the standing-
    jobs daemon knobs (enabled, cron, quiet_hours, raise_ttl_hours,
    max_runs_per_hour). Mirrors `load_evolution()`. Empty dict if absent."""
    return load_config(path, force_reload=force_reload).get("jobs", {})


def load_job_definitions(path: Path | None = None, *, force_reload: bool = False) -> list[dict[str, Any]]:
    """Load the standing-job definitions from config/jobs.yaml (v0.5 phase 06).

    Each entry: `name`, `schedule` (interval seconds), `grant_bundle` (capability
    classes), `prompt`, and an `enabled` flag. The file is optional — a missing
    file means no jobs (returns []). Parse errors raise ConfigError so the daemon
    fails friendly rather than spawning malformed work.
    """
    _ensure_dotenv()
    jobs_path = (path or _DEFAULT_JOBS_PATH).resolve()
    if jobs_path in _cache and not force_reload:
        return _cache[jobs_path].get("jobs", [])
    if not jobs_path.exists():
        return []
    try:
        with jobs_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"jobs.yaml is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("jobs", []), list):
        raise ConfigError("jobs.yaml: expected a top-level `jobs:` list")
    cfg = _resolve_env_refs(raw)
    _cache[jobs_path] = cfg
    return cfg.get("jobs", [])


def reload() -> None:
    _cache.clear()
