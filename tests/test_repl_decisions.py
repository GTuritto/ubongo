from __future__ import annotations

from pathlib import Path

import pytest

from ubongo import repl
from ubongo.memory import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _seed_decision(intent="technical", persona="architect", action="auto", risk="low", conf=0.9,
                   reversibility="reversible"):
    cid = store.start_conversation(persona)
    msg_id = store.append_message(cid, "user", "x", persona=persona)
    wf_id = store.append_workflow_run(
        conversation_id=cid,
        message_id=msg_id,
        classification={"intent": intent, "confidence": conf},
        workflow={"persona": persona, "execution_mode": "sequential"},
        execution_mode="sequential",
        outcome="success",
        started_at=store.now_iso(),
    )
    return store.append_governance_decision(
        workflow_run_id=wf_id,
        intent=intent,
        risk=risk,
        confidence=conf,
        reversibility=reversibility,
        action=action,
    )


def test_render_decisions_shows_reversibility_column():
    _seed_decision(action="require_approval", risk="destructive", reversibility="irreversible")
    out = repl._render_decisions_table(10)
    assert "irreversible" in out
    assert "require_approval" in out


def test_parse_decisions_no_arg_defaults_to_10():
    assert repl._parse_decisions_command("/decisions") == 10


def test_parse_decisions_explicit_arg():
    assert repl._parse_decisions_command("/decisions 25") == 25


def test_parse_decisions_rejects_non_int():
    assert repl._parse_decisions_command("/decisions abc") is None


def test_parse_decisions_rejects_zero_or_negative():
    assert repl._parse_decisions_command("/decisions 0") is None
    assert repl._parse_decisions_command("/decisions -3") is None


def test_render_empty_decisions():
    assert repl._render_decisions_table() == "No decisions yet."


def test_render_decisions_shows_rows_newest_first():
    _seed_decision(intent="technical", persona="architect")
    _seed_decision(intent="casual", persona="casual")
    _seed_decision(intent="research", persona="architect")

    table = repl._render_decisions_table()
    lines = table.splitlines()
    assert lines[0] == "Recent decisions (last 10):"
    assert "research" in lines[1]
    assert "casual" in lines[2]
    assert "technical" in lines[3]


def test_render_decisions_respects_n():
    for i in range(5):
        _seed_decision(intent=f"intent{i}", persona="casual")
    table = repl._render_decisions_table(n=2)
    lines = table.splitlines()
    assert lines[0] == "Recent decisions (last 2):"
    assert len(lines) == 3  # header + 2 rows


def test_help_line_mentions_decisions():
    assert "/decisions" in repl._HELP_COMMANDS
