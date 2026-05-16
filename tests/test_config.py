from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import config  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    config.reload()
    yield
    config.reload()


def _write_settings(path: Path, classifier_model: str) -> None:
    path.write_text(
        "models:\n"
        f"  classifier: {classifier_model}\n"
        "  default: openrouter/anthropic/claude-sonnet-4.5\n"
        "  casual: openrouter/anthropic/claude-haiku-4.5\n"
        "  compaction: openrouter/anthropic/claude-haiku-4.5\n"
        "  research: openrouter/anthropic/claude-sonnet-4.5\n"
        "  evaluator: openrouter/anthropic/claude-sonnet-4.5\n"
        "  critic: openrouter/anthropic/claude-sonnet-4.5\n"
        "  coding: openrouter/anthropic/claude-sonnet-4.5\n"
        "  evolution_generator: openrouter/anthropic/claude-sonnet-4.5\n"
        "\n"
        "api_keys:\n"
        "  openrouter:\n"
        "    env: OPENROUTER_API_KEY\n"
        "\n"
        "logging:\n"
        "  level: INFO\n"
        "  format: json\n",
        encoding="utf-8",
    )


def test_two_paths_cache_independently(tmp_path: Path):
    """Regression for review finding #5: load_config used to ignore the `path`
    argument after the first call. Two distinct config files in the same
    process now coexist correctly."""
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    _write_settings(a, "model-a")
    _write_settings(b, "model-b")

    cfg_a = config.load_config(path=a)
    cfg_b = config.load_config(path=b)

    assert cfg_a["models"]["classifier"] == "model-a"
    assert cfg_b["models"]["classifier"] == "model-b"
    # And cached: second load returns the same dict identity per path.
    assert config.load_config(path=a) is cfg_a
    assert config.load_config(path=b) is cfg_b


def test_force_reload_rereads_same_path(tmp_path: Path):
    p = tmp_path / "settings.yaml"
    _write_settings(p, "v1")
    cfg1 = config.load_config(path=p)
    assert cfg1["models"]["classifier"] == "v1"
    _write_settings(p, "v2")
    cfg_cached = config.load_config(path=p)
    assert cfg_cached["models"]["classifier"] == "v1"  # cache hit
    cfg_fresh = config.load_config(path=p, force_reload=True)
    assert cfg_fresh["models"]["classifier"] == "v2"


def test_reload_clears_all_cached_paths(tmp_path: Path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    _write_settings(a, "model-a")
    _write_settings(b, "model-b")
    config.load_config(path=a)
    config.load_config(path=b)
    config.reload()
    # After reload(), both should re-read on next call (new identity).
    cfg_a = config.load_config(path=a)
    cfg_b = config.load_config(path=b)
    assert cfg_a["models"]["classifier"] == "model-a"
    assert cfg_b["models"]["classifier"] == "model-b"


# --- Phase 14a: governance.yaml loader ---


def test_load_governance_reads_the_real_file():
    """The shipped config/governance.yaml loads and has the matrix keys."""
    gov = config.load_governance()
    assert "thresholds" in gov
    assert gov["thresholds"]["reject_below_confidence"] == 0.2
    assert gov["thresholds"]["clarification_below_confidence"] == 0.5
    assert gov["thresholds"]["critic_band"] == [0.2, 0.6]
    assert "destructive" in gov["require_approval"]["risks"]
    assert gov["require_approval"]["irreversible_high_risk"] is True
    assert isinstance(gov["destructive_keywords"], list)
    assert "rm -rf" in gov["destructive_keywords"]


def test_load_governance_caches_and_force_reload(tmp_path: Path):
    p = tmp_path / "governance.yaml"
    p.write_text("thresholds:\n  reject_below_confidence: 0.1\n", encoding="utf-8")
    g1 = config.load_governance(path=p)
    assert g1["thresholds"]["reject_below_confidence"] == 0.1
    assert config.load_governance(path=p) is g1  # cache hit
    p.write_text("thresholds:\n  reject_below_confidence: 0.3\n", encoding="utf-8")
    assert config.load_governance(path=p)["thresholds"]["reject_below_confidence"] == 0.1
    fresh = config.load_governance(path=p, force_reload=True)
    assert fresh["thresholds"]["reject_below_confidence"] == 0.3


def test_load_governance_missing_file_raises(tmp_path: Path):
    with pytest.raises(config.ConfigError, match="governance.yaml not found"):
        config.load_governance(path=tmp_path / "nope.yaml")
