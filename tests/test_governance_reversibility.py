from __future__ import annotations

from ubongo.governance.reversibility import Reversibility, score_reversibility


def _workflow(agents=(), skill_name=None):
    return type("W", (), {"agents": tuple(agents), "skill_name": skill_name})()


def test_plain_persona_turn_is_reversible():
    assert score_reversibility(_workflow(agents=("architect",))) is Reversibility.REVERSIBLE


def test_research_turn_is_reversible():
    assert score_reversibility(_workflow(agents=("research", "architect"))) is Reversibility.REVERSIBLE


def test_execution_agent_makes_turn_irreversible():
    wf = _workflow(agents=("execution", "architect"))
    assert score_reversibility(wf) is Reversibility.IRREVERSIBLE


def test_irreversible_skill_makes_turn_irreversible():
    # constrained-bash ships with reversibility: irreversible.
    wf = _workflow(agents=("architect",), skill_name="constrained-bash")
    assert score_reversibility(wf) is Reversibility.IRREVERSIBLE


def test_reversible_skill_stays_reversible():
    wf = _workflow(agents=("architect",), skill_name="summarize-conversation")
    assert score_reversibility(wf) is Reversibility.REVERSIBLE


def test_unknown_skill_name_is_ignored():
    wf = _workflow(agents=("architect",), skill_name="phantom-skill")
    assert score_reversibility(wf) is Reversibility.REVERSIBLE
