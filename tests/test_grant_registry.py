"""The grant registry (v0.5 phase 05): persistent capability grants that turn
ask-every-time into ask-once. Covers grant_state CRUD, the governance grant-check
rule, the approval->grant link, and the management surface."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.governance import grants as gov_grants  # noqa: E402
from ubongo.governance.decision import Action, decide  # noqa: E402
from ubongo.memory import grant_state, store  # noqa: E402

_GOV = {
    "thresholds": {"reject_below_confidence": 0.2, "clarification_below_confidence": 0.5,
                   "critic_band": [0.2, 0.6]},
    "require_approval": {"risks": ["destructive"], "irreversible_high_risk": True},
    "destructive_keywords": ["rm -rf", "delete the entire", "wipe"],
}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def _classification(**o):
    base = {"intent": "technical", "tone": "neutral", "task_type": "question",
            "suggested_skill": None, "risk": "low", "confidence": 0.9}
    base.update(o)
    return type("Classification", (), base)()


def _workflow(agents=("architect",)):
    return type("Workflow", (), {"persona": "architect", "model": "m",
                                 "skill_name": None, "execution_mode": "sequential",
                                 "agents": tuple(agents)})()


def _result():
    return type("WorkflowResult", (), {"evaluator_confidence": None})()


def _fake_servers(*names, risk="low"):
    # low-risk by default so the ADR-0016 risk escalation + rule 2 (high+irreversible)
    # don't fire ahead of the grant rule — these tests isolate the grant rule.
    return [type("S", (), {"name": n, "risk": risk})() for n in names]


def _decide(workflow, message="use the tool", classification=None):
    return decide(classification or _classification(), workflow, _result(),
                  message=message, governance=_GOV)


# --- grant_state CRUD ---

def test_grant_and_is_granted():
    assert not grant_state.is_granted("connector:compendium")
    gid = grant_state.grant("connector:compendium")
    assert gid > 0
    assert grant_state.is_granted("connector:compendium")


def test_revoke_rearms_and_is_idempotent():
    gid = grant_state.grant("connector:x")
    assert grant_state.revoke(gid) is True
    assert grant_state.is_granted("connector:x") is False
    assert grant_state.revoke(gid) is False  # already revoked


def test_active_grants_lists_only_active():
    a = grant_state.grant("connector:a")
    grant_state.grant("connector:b")
    grant_state.revoke(a)
    classes = {g["capability_class"] for g in grant_state.active_grants()}
    assert classes == {"connector:b"}


def test_scope_star_covers_any_agent():
    grant_state.grant("connector:s", scope="*")
    assert grant_state.is_granted("connector:s", scope="connector")


# --- governance/grants helpers ---

def test_capability_classes_from_enabled_servers():
    wf = _workflow(agents=("connector", "architect"))
    with patch("ubongo.mcp.client.servers", return_value=_fake_servers("compendium", "news")):
        assert gov_grants.capability_classes(wf) == ["connector:compendium", "connector:news"]


def test_non_connector_turn_has_no_classes():
    assert gov_grants.capability_classes(_workflow(("architect",))) == []


# --- the decision rule ---

def test_connector_turn_without_grant_gates_first_encounter():
    wf = _workflow(agents=("connector", "architect"))
    with patch("ubongo.mcp.client.servers", return_value=_fake_servers("compendium")):
        d = _decide(wf)
    assert d.action == Action.REQUIRE_APPROVAL.value
    assert d.reason == "grant_first_encounter:connector:compendium"


def test_connector_turn_with_grant_falls_through_to_auto():
    grant_state.grant("connector:compendium")
    wf = _workflow(agents=("connector", "architect"))
    with patch("ubongo.mcp.client.servers", return_value=_fake_servers("compendium")):
        d = _decide(wf)
    assert d.action == Action.AUTO.value


def test_destructive_connector_turn_still_gates_on_safety_with_grant():
    grant_state.grant("connector:compendium")
    wf = _workflow(agents=("connector", "architect"))
    with patch("ubongo.mcp.client.servers", return_value=_fake_servers("compendium")):
        d = _decide(wf, message="delete the entire vault")
    # rule 1 (destructive) fires before the grant rule
    assert d.action == Action.REQUIRE_APPROVAL.value
    assert d.reason == "risk_destructive"


def test_non_connector_turn_writes_no_grant_and_autos():
    d = _decide(_workflow(("architect",)))
    assert d.action == Action.AUTO.value
    assert grant_state.active_grants() == []


# --- approval -> grant link ---

def test_grant_connector_turn_persists_ungranted_classes():
    wf = _workflow(agents=("connector", "architect"))
    with patch("ubongo.mcp.client.servers", return_value=_fake_servers("compendium")):
        ids = gov_grants.grant_connector_turn(wf)
        assert len(ids) == 1
        # second call is a no-op — already granted
        assert gov_grants.grant_connector_turn(wf) == []
    assert grant_state.is_granted("connector:compendium")


# --- management surface (REPL /grants) ---

def test_repl_grants_list_and_revoke():
    from ubongo import repl
    gid = grant_state.grant("connector:compendium")
    st = repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None)
    listed = repl._cmd_grants("/grants", st)
    assert f"#{gid}" in listed and "connector:compendium" in listed
    revoked = repl._cmd_grants(f"/grants revoke {gid}", st)
    assert "Revoked" in revoked
    assert not grant_state.is_granted("connector:compendium")
    assert "No active grants" in repl._cmd_grants("/grants", st)


def test_repl_grants_unknown_revoke():
    from ubongo import repl
    st = repl.ReplState(persona="casual", auto_mode=False, pending_skill=None, pending_workflow=None)
    out = repl._cmd_grants("/grants revoke 9999", st)
    assert "No active grant #9999" in out


def test_grants_in_help_banner():
    from ubongo import repl
    assert "/grants" in repl._HELP_COMMANDS


# --- CLI surface (ubongo grants) ---

def test_cli_grants_list_and_revoke(capsys):
    from ubongo import oneshot
    gid = grant_state.grant("connector:compendium")
    assert oneshot.grants() == 0
    assert "connector:compendium" in capsys.readouterr().out
    assert oneshot.grants(revoke_id=gid) == 0
    assert "Revoked" in capsys.readouterr().out
    assert oneshot.grants(revoke_id=9999) == 1  # unknown


# --- master end-to-end: ask once, then auto, then revoke re-arms ---

def _completion(text):
    from ubongo.llm import CompletionResult
    return CompletionResult(text=text, model="m", tokens_in=1, tokens_out=1, latency_ms=1, attempts=1)


def test_master_ask_once_then_auto_then_revoke():
    from unittest.mock import patch
    from ubongo import master
    from ubongo.classifier import Classification

    cls = Classification(intent="work", tone="neutral", task_type="command", risk="low",
                         confidence=1.0, suggested_skill=None)
    connector_wf = master.Workflow(persona="architect", model="m", skill_name=None,
                                   execution_mode="sequential", agents=("connector", "architect"))

    with patch("ubongo.mcp.client.servers", return_value=_fake_servers("compendium")), \
         patch("ubongo.master.classifier.classify", return_value=cls), \
         patch("ubongo.master.MasterAgent.plan", return_value=connector_wf), \
         patch("ubongo.agents.personas.complete", return_value=_completion("tool answer")), \
         patch("ubongo.agents.connector.ConnectorAgent.run") as conn_run:
        from ubongo.agents.base import AgentResult
        conn_run.return_value = AgentResult(text="[Connector] ok", ok=True, model="m",
                                            tokens_in=1, tokens_out=1, latency_ms=1)

        # 1. first connector turn with no grant -> gated
        r1 = master.handle("use compendium", "architect")
        assert r1.approval is not None
        assert not grant_state.is_granted("connector:compendium")

        # 2. approve -> grant written, answer delivered
        r2 = master.resume_approval(r1.approval.decision_id, "y")
        assert r2 is not None
        assert grant_state.is_granted("connector:compendium")

        # 3. next connector turn auto-proceeds (no gate)
        r3 = master.handle("use compendium again", "architect")
        assert r3.approval is None

        # 4. revoke -> next turn re-arms the ask
        gid = grant_state.active_grants()[0]["id"]
        grant_state.revoke(gid)
        r4 = master.handle("use compendium once more", "architect")
        assert r4.approval is not None
