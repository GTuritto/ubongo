from __future__ import annotations

import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import config, repl  # noqa: E402


def test_reload_all_clears_config_cache() -> None:
    # warm the config cache, then hot-reload
    config.load_config()
    assert config._cache  # non-empty after a load
    msg = repl._reload_all()
    assert config._cache == {}  # Phase 21e: settings cache cleared
    assert "settings" in msg and "routing" in msg


def test_reload_all_reads_new_config(tmp_path, monkeypatch) -> None:
    # a settings.yaml whose casual model differs, applied after reload
    import yaml
    base = config.load_config()
    edited = {**base, "models": {**base["models"], "casual": "openrouter/test/changed-model"}}
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.safe_dump(edited))
    monkeypatch.setattr(config, "_DEFAULT_SETTINGS_PATH", p)
    repl._reload_all()  # clears cache so the next load reads the edited file
    assert config.load_config()["models"]["casual"] == "openrouter/test/changed-model"
