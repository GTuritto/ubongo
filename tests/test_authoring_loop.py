from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.authoring import candidate, loop, quarantine  # noqa: E402
from ubongo.authoring import sandbox as eval_sandbox  # noqa: E402
from ubongo.authoring.loop import AuthoringLoop, _should_cycle, run_one_cycle  # noqa: E402
from ubongo.llm import CompletionResult  # noqa: E402
from ubongo.memory import authoring_state
from ubongo.memory import store, vault  # noqa: E402

_DRAFT_JSON = (
    '{"name": "french-translator", "description": "translate text to French", '
    '"risk": "low", "reversibility": "reversible", "default_persona": "operator", '
    '"body": "Translate the text to French.", "prompts": {}, "command_template": null}'
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "t.db")
    store.bootstrap()
    quarantine.set_candidates_dir(tmp_path / "cand")
    monkeypatch.setattr(vault, "append_audit_entry", lambda *a, **k: None)
    monkeypatch.setattr(candidate, "complete", lambda **k: CompletionResult(
        text=_DRAFT_JSON, model="m", tokens_in=1, tokens_out=1, latency_ms=1, attempts=1))
    yield tmp_path
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    quarantine.set_candidates_dir(None)


def _seed_gap(intent="translation", n=3):
    conn = store.connection()
    conv = store.start_conversation("architect")
    for i in range(n):
        mid = store.append_message(conversation_id=conv, role="user", content=f"translate #{i}")
        cls = json.dumps({"intent": intent, "suggested_skill": None, "confidence": 0.9})
        conn.execute(
            "INSERT INTO workflow_runs (conversation_id, message_id, classification, workflow, "
            "execution_mode, started_at, outcome) VALUES (?,?,?,?,?,?, 'success')",
            (conv, mid, cls, "{}", "sequential", store.now_iso()),
        )


# --- pure gate --------------------------------------------------------------

def test_should_cycle_requires_running() -> None:
    assert not _should_cycle(status="paused", remaining=10, seconds_since_last=None, cron=None)
    assert _should_cycle(status="running", remaining=10, seconds_since_last=None, cron=None)


def test_should_cycle_respects_budget() -> None:
    assert not _should_cycle(status="running", remaining=0, seconds_since_last=None, cron=None)


def test_should_cycle_respects_cron() -> None:
    assert not _should_cycle(status="running", remaining=10, seconds_since_last=5.0, cron=60)
    assert _should_cycle(status="running", remaining=10, seconds_since_last=90.0, cron=60)


# --- cycle ------------------------------------------------------------------

def test_cycle_drafts_for_gap(env) -> None:
    _seed_gap()
    r = run_one_cycle()
    assert r.action == "drafted"
    assert r.candidate_id is not None and r.gap == "translation"
    drafts = authoring_state.authored_skills(status="draft")
    assert drafts and drafts[0]["source"] == "auto"
    # a run row was recorded
    assert authoring_state.authoring_runs_recent(1)[0]["outcome"] == "drafted"


def test_cycle_idle_when_no_gap(env) -> None:
    r = run_one_cycle()
    assert r.action == "idle"
    assert authoring_state.authored_skills(status="draft") == []


def test_worked_gap_not_redrafted(env) -> None:
    _seed_gap()
    run_one_cycle()
    r2 = run_one_cycle()  # gap already worked
    assert r2.action == "idle"
    assert len(authoring_state.authored_skills(status="draft")) == 1


def test_crash_recovery_reevaluates_unevaluated_draft(env, monkeypatch) -> None:
    # Seed an auto draft with no quality (as if a crash interrupted eval).
    cand = {"name": "x-skill", "description": "do x", "risk": "low",
            "reversibility": "reversible", "default_persona": None, "body": "b",
            "prompts": {}, "command_template": None, "metadata": {}}
    cid = authoring_state.append_authored_skill(name="x-skill", description="do x", status="draft",
                                      generation=1, source="auto", candidate=cand)
    assert authoring_state.get_authored_skill(cid)["quality"] is None

    monkeypatch.setenv("UBONGO_DISABLE_AUTHORING_EVAL", "0")
    monkeypatch.setattr(eval_sandbox, "complete", lambda **k: CompletionResult(
        text=('{"quality":0.7,"hallucination":0.1,"would_user_correct":false}'
              if k["messages"][0]["content"].startswith("Score") else "ok"),
        model="m", tokens_in=2, tokens_out=2, latency_ms=4, attempts=1))

    r = run_one_cycle()
    assert r.action == "reevaluated" and r.candidate_id == cid
    assert authoring_state.get_authored_skill(cid)["quality"] is not None


# --- daemon control ---------------------------------------------------------

def test_disabled_env_does_not_start(env) -> None:
    # conftest sets UBONGO_DISABLE_AUTHORING=1
    assert AuthoringLoop().start() is False


def test_boots_paused_when_enabled(env, monkeypatch) -> None:
    monkeypatch.delenv("UBONGO_DISABLE_AUTHORING", raising=False)
    daemon = AuthoringLoop(tick_seconds=0.05)
    started = daemon.start()
    try:
        assert started is True
        assert authoring_state.get_authoring_status() == "paused"  # never auto-spends on launch
    finally:
        daemon.stop()


def test_status_control(env) -> None:
    assert authoring_state.get_authoring_status() == "paused"
    authoring_state.set_authoring_status("running")
    assert authoring_state.get_authoring_status() == "running"
    authoring_state.set_authoring_status("off")
    assert authoring_state.get_authoring_status() == "off"
