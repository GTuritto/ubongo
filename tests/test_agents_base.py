from __future__ import annotations

import pytest

from ubongo.agents.base import Agent, AgentInput, AgentResult


def test_agent_input_is_frozen_with_expected_fields():
    inp = AgentInput(
        message="hi",
        history=({"role": "user", "content": "hi"},),
        summary_text=None,
        prior_findings=(),
    )
    assert inp.message == "hi"
    assert inp.history == ({"role": "user", "content": "hi"},)
    assert inp.summary_text is None
    assert inp.prior_findings == ()
    assert inp.metadata == {}
    with pytest.raises((AttributeError, Exception)):
        inp.message = "no"  # type: ignore[misc]


def test_agent_result_is_frozen_with_expected_fields():
    r = AgentResult(
        text="findings",
        ok=True,
        model="m",
        tokens_in=10,
        tokens_out=20,
        latency_ms=42,
    )
    assert r.text == "findings"
    assert r.ok is True
    assert r.confidence is None
    assert r.error is None
    assert r.metadata == {}
    with pytest.raises((AttributeError, Exception)):
        r.ok = False  # type: ignore[misc]


def test_protocol_isinstance_check_passes_for_conformant_class():
    class Toy:
        name = "toy"
        role = "toy role"
        default_model = "test"

        def run(self, input, context):
            return AgentResult(text="x", ok=True, model="test", tokens_in=0, tokens_out=0, latency_ms=0)

    assert isinstance(Toy(), Agent)


def test_protocol_isinstance_check_fails_when_run_missing():
    class Broken:
        name = "broken"
        role = "broken role"
        default_model = "test"

    assert not isinstance(Broken(), Agent)


def test_metadata_default_is_independent_per_instance():
    a = AgentInput(message="a", history=(), summary_text=None, prior_findings=())
    b = AgentInput(message="b", history=(), summary_text=None, prior_findings=())
    a.metadata["k"] = "v"
    assert b.metadata == {}


def test_result_metadata_default_is_independent_per_instance():
    a = AgentResult(text="", ok=True, model=None, tokens_in=0, tokens_out=0, latency_ms=0)
    b = AgentResult(text="", ok=True, model=None, tokens_in=0, tokens_out=0, latency_ms=0)
    a.metadata["k"] = "v"
    assert b.metadata == {}
