from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import context, events, master, router, skills  # noqa: E402
from ubongo.memory import store, vault  # noqa: E402
from ubongo.repl import (  # noqa: E402
    _MODE_LIST_SENTINEL,
    _parse_mode_command,
    _render_mode_list,
)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    vault.set_vault_root(tmp_path / "vault")
    skills.set_skills_dir(None)
    skills.reload()
    router.reload()
    context.reload()
    events.clear()
    yield
    events.clear()
    skills.set_skills_dir(None)
    skills.reload()
    router.reload()
    context.reload()
    store.set_db_path(None)
    vault.set_vault_root(None)


# --- parser ---


def test_parse_mode_returns_workflow_name():
    assert _parse_mode_command("/mode coding_competitive") == "coding_competitive"


def test_parse_mode_list_returns_sentinel():
    assert _parse_mode_command("/mode list") == _MODE_LIST_SENTINEL
    assert _parse_mode_command("/mode LIST") == _MODE_LIST_SENTINEL


def test_parse_mode_no_arg_returns_none():
    assert _parse_mode_command("/mode") is None
    assert _parse_mode_command("/mode    ") is None


def test_parse_mode_returns_none_for_other_commands():
    assert _parse_mode_command("/skill x") is None
    assert _parse_mode_command("/exec ls") is None


# --- renderer ---


def test_render_mode_list_includes_all_workflows():
    out = _render_mode_list()
    assert out.startswith("Available workflows:")
    # Phase 11 + Phase 12 workflows all visible.
    assert "technical_deep" in out
    assert "research_brief_parallel" in out
    assert "coding_competitive" in out
    assert "brief_collaborative" in out
    assert "debate_then_synthesize" in out
    assert "speculative_brief" in out
    # Mode badges visible.
    assert "mode=parallel" in out
    assert "mode=competitive" in out
    assert "mode=debate" in out
    assert "mode=speculative" in out


# --- end-to-end via master.handle ---


def test_pending_workflow_overrides_routing(tmp_path):
    """When pending_workflow is set, master.plan uses that workflow regardless
    of classification. We capture the resulting workflow via Master.execute."""
    captured: dict = {}

    real_execute = master.MasterAgent.execute

    def _capture_execute(self, workflow, ctx, message, workflow_run_id=None):
        captured["execution_mode"] = workflow.execution_mode
        captured["agents"] = workflow.agents
        captured["persona"] = workflow.persona
        # Return a happy WorkflowResult so master.handle completes.
        return master.WorkflowResult(
            text="ok", ok=True, tokens_in=1, tokens_out=1,
            model="m", latency_ms=1,
        )

    with patch.object(master.MasterAgent, "execute", _capture_execute):
        master.handle(
            "any message",
            persona_name="casual",
            auto_mode=False,
            pending_workflow="coding_competitive",
        )
    assert captured["execution_mode"] == "competitive"
    assert captured["agents"] == ("coding", "architect", "evaluator")


def test_pending_workflow_unknown_falls_back_to_routing(tmp_path):
    """Unknown workflow name in pending_workflow is silently ignored
    (the REPL validates before setting; this is defensive)."""
    captured: dict = {}

    def _capture_execute(self, workflow, ctx, message, workflow_run_id=None):
        captured["execution_mode"] = workflow.execution_mode
        return master.WorkflowResult(
            text="ok", ok=True, tokens_in=1, tokens_out=1,
            model="m", latency_ms=1,
        )

    with patch.object(master.MasterAgent, "execute", _capture_execute):
        master.handle(
            "hi",
            persona_name="casual",
            auto_mode=False,
            pending_workflow="phantom-workflow",
        )
    # Falls back to casual_reply (sequential) since classification was casual.
    assert captured["execution_mode"] == "sequential"
