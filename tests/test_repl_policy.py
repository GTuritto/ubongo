from __future__ import annotations

from ubongo import repl


def test_render_policy_shows_thresholds():
    out = repl._render_policy()
    assert "Governance policy" in out
    assert "reject_below_confidence:" in out
    assert "clarification_below_confidence:" in out
    assert "critic_band:" in out


def test_render_policy_shows_require_approval_rules():
    out = repl._render_policy()
    assert "require_approval:" in out
    assert "irreversible_high_risk:" in out
    assert "destructive" in out


def test_render_policy_lists_destructive_keywords():
    out = repl._render_policy()
    assert "destructive_keywords" in out
    assert "rm -rf" in out


def test_render_policy_shows_the_matrix_rules():
    out = repl._render_policy()
    assert "require_approval" in out
    assert "reject" in out
    assert "ask_clarification" in out
    assert "auto" in out


def test_help_banner_includes_policy():
    assert "/policy" in repl._HELP_COMMANDS
