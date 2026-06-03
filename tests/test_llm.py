from __future__ import annotations

import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import llm  # noqa: E402


class _Msg:
    content = '{"ok": true}'


class _Choice:
    message = _Msg()


class _Usage:
    prompt_tokens = 1
    completion_tokens = 1


class _Resp:
    choices = [_Choice()]
    usage = _Usage()


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def _fake_completion(**kwargs):
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(llm.litellm, "completion", _fake_completion)
    return captured


def test_complete_forwards_temperature_when_set(monkeypatch) -> None:
    captured = _capture(monkeypatch)
    llm.complete("sys", [{"role": "user", "content": "hi"}], model="m", max_tokens=10, temperature=0)
    assert captured["temperature"] == 0


def test_complete_omits_temperature_by_default(monkeypatch) -> None:
    captured = _capture(monkeypatch)
    llm.complete("sys", [{"role": "user", "content": "hi"}], model="m", max_tokens=10)
    assert "temperature" not in captured  # provider/model default left intact
