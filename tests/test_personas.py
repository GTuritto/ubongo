from __future__ import annotations

import os

import pytest

# Ensure config loads even if .env is missing during isolated test runs
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.agents import personas  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_persona_registry():
    personas.reload()
    yield
    personas.reload()


@pytest.mark.parametrize(
    "name,expected_model_substring",
    [
        ("architect", "claude-sonnet-4.5"),
        ("operator", "claude-sonnet-4.5"),
        ("casual", "claude-haiku-4.5"),
    ],
)
def test_get_resolves_model_via_settings(name: str, expected_model_substring: str) -> None:
    p = personas.get(name)
    assert p.name == name
    assert expected_model_substring in p.model
    assert p.max_tokens == 1024
    assert p.body  # non-empty


def test_get_caches_after_first_load() -> None:
    a = personas.get("architect")
    b = personas.get("architect")
    assert a is b


def test_get_unknown_persona_raises() -> None:
    with pytest.raises(FileNotFoundError):
        personas.get("phantom")


def test_persona_body_excludes_frontmatter() -> None:
    p = personas.get("architect")
    assert "---" not in p.body.split("\n", 1)[0]
    assert "default_model" not in p.body
