from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, skills  # noqa: E402
from ubongo.master import Response  # noqa: E402
from ubongo.mcp import service  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


def _response(text="hi", ok=True, approval=None, requires_user_decision=False):
    return Response(
        text=text, ok=ok, persona="architect", skill_name=None,
        delivery_token=None, approval=approval,
        requires_user_decision=requires_user_decision,
    )


@pytest.fixture()
def _stub_turn(monkeypatch):
    calls = {}

    def handle(message, persona, auto_mode=False, **kwargs):
        calls["args"] = (message, persona, auto_mode)
        return calls["response"]

    monkeypatch.setattr(service.master, "handle", handle)
    monkeypatch.setattr(service.queue, "flush_delivered", lambda token: None)
    return calls


# ---------- send_turn ----------


def test_send_turn_happy_path(_stub_turn):
    _stub_turn["response"] = _response("answer")
    out = service.send_turn("hello", "architect")
    assert out == {
        "text": "answer", "ok": True, "persona": "architect",
        "gated": False, "requires_user_decision": False,
    }
    assert _stub_turn["args"] == ("hello", "architect", False)


def test_send_turn_default_persona_and_auto(_stub_turn):
    _stub_turn["response"] = _response()
    service.send_turn("hello", None, auto=True)
    assert _stub_turn["args"] == ("hello", "architect", True)


def test_send_turn_gated_reports_and_is_not_approvable(_stub_turn):
    _stub_turn["response"] = _response(
        "This looks destructive...", ok=True,
        approval={"decision_id": 1, "summary": "s", "why": "w"},
    )
    out = service.send_turn("delete the entire vault")
    assert out["gated"] is True
    # the approval payload is NOT forwarded over MCP
    assert "approval" not in out and "decision_id" not in str(out)


def test_send_turn_repair_exhausted(_stub_turn):
    _stub_turn["response"] = _response("apology", ok=False, requires_user_decision=True)
    out = service.send_turn("hello")
    assert out["ok"] is False
    assert out["requires_user_decision"] is True


def test_send_turn_bad_persona_never_reaches_master(_stub_turn):
    _stub_turn["response"] = _response()
    out = service.send_turn("hello", "bogus")
    assert out["ok"] is False
    assert "Unknown persona 'bogus'" in out["text"]
    assert "args" not in _stub_turn  # master.handle never called


# ---------- recall_view ----------


def test_recall_view_empty_db():
    out = service.recall_view()
    assert out == {"summary": "", "recency": [], "semantic": []}


def test_recall_view_returns_recency_rows():
    conv = store.current_or_new_conversation("architect")
    store.append_message(conv, "user", "we chose redis", persona="architect")
    out = service.recall_view("redis")
    assert any("we chose redis" in row for row in out["recency"])
    assert out["semantic"] == []  # embeddings disabled in the suite


# ---------- resources ----------


def test_daily_note_text_missing_note():
    assert service.daily_note_text() == "(no daily note yet today)"


def test_daily_note_text_reads_today():
    from datetime import date
    path = vault.daily_note_path(date.today())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# today\nhello note\n", encoding="utf-8")
    assert "hello note" in service.daily_note_text()


def test_audit_text_empty_and_populated():
    assert service.audit_text() == "(audit log empty)"
    vault.append_audit_entry("governance", "gated something")
    out = service.audit_text()
    assert "[governance]" in out and "gated something" in out
