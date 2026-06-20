from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import channel, profiling  # noqa: E402
from ubongo.master import Response  # noqa: E402
from ubongo.memory import store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")  # profiles dir lands in tmp
    yield
    store.set_db_path(None)


def _response(text="hi"):
    return Response(text=text, ok=True, persona="architect",
                    skill_name=None, delivery_token=None)


@pytest.fixture()
def _stub(monkeypatch):
    calls = {}

    def handle(message, persona, auto_mode=False, pending_skill=None,
               pending_workflow=None, approved=False, pending_verbosity=None):
        calls["kwargs"] = dict(
            message=message, persona=persona, auto_mode=auto_mode,
            pending_skill=pending_skill, pending_workflow=pending_workflow,
            approved=approved, pending_verbosity=pending_verbosity,
        )
        return _response()

    monkeypatch.setattr(channel.master, "handle", handle)
    flushed = []
    monkeypatch.setattr(channel.queue, "flush_delivered", flushed.append)
    calls["flushed"] = flushed
    return calls


# ---------- bootstrap ----------


def test_bootstrap_is_idempotent_and_resolves_knob(monkeypatch):
    fake_cfg = {"logging": {"level": "INFO"}}
    monkeypatch.setenv("UBONGO_PROFILE", "cpu")
    with patch("ubongo.channel.load_config", return_value=fake_cfg), \
         patch("ubongo.channel.setup_logging") as m_setup:
        channel._bootstrapped = False
        channel._startup_profile = None
        channel.bootstrap("test")
        channel.bootstrap("test")
    assert m_setup.call_count == 1
    assert channel.cpu_armed() is True
    channel._bootstrapped = False
    channel._startup_profile = None


# ---------- run_turn ----------


def test_run_turn_passes_everything_through_and_flushes(_stub):
    response, report = channel.run_turn(
        "msg", "operator", auto_mode=True, approved=True,
        pending_skill="s", pending_workflow="w", profile_cpu=False,
    )
    assert response.text == "hi"
    assert report is None
    assert _stub["kwargs"] == dict(
        message="msg", persona="operator", auto_mode=True,
        pending_skill="s", pending_workflow="w", approved=True,
        pending_verbosity=None,
    )
    assert _stub["flushed"] == [response.delivery_token]


def test_run_turn_profile_cpu_writes_artifact_and_returns_report(_stub):
    response, report = channel.run_turn("msg", "architect", profile_cpu=True)
    assert response.text == "hi"
    assert report is not None and "CPU profile written to" in report
    assert list(profiling.profiles_dir().glob("turn-*.prof"))


def test_run_turn_default_uses_bootstrap_knob(_stub, monkeypatch):
    monkeypatch.setattr(channel, "_startup_profile", "cpu")
    _, report = channel.run_turn("msg", "architect")
    assert report is not None
    monkeypatch.setattr(channel, "_startup_profile", None)
    _, report = channel.run_turn("msg", "architect")
    assert report is None


def test_string_path_patch_survives_the_seam():
    """Channels' tests patch the shared master module attribute; prove the
    seam sees such patches (master.handle resolved at call time)."""
    resp = _response("patched")
    with patch("ubongo.master.handle", return_value=resp), \
         patch("ubongo.channel.queue.flush_delivered"):
        response, _ = channel.run_turn("x", "casual", profile_cpu=False)
    assert response.text == "patched"
