from __future__ import annotations

import pytest

from ubongo import events


@pytest.fixture(autouse=True)
def _reset_event_bus():
    events.clear()
    yield
    events.clear()


def test_dispatch_with_no_handlers_is_a_noop() -> None:
    events.dispatch("nothing_listens", {"k": 1})


def test_register_then_dispatch_calls_handler_with_payload() -> None:
    received: list[dict] = []
    events.register("before_llm", received.append)
    events.dispatch("before_llm", {"model": "x"})
    assert received == [{"model": "x"}]


def test_handlers_run_in_registration_order() -> None:
    order: list[str] = []
    events.register("evt", lambda _: order.append("first"))
    events.register("evt", lambda _: order.append("second"))
    events.dispatch("evt", {})
    assert order == ["first", "second"]


def test_handler_exception_is_swallowed_and_chain_continues() -> None:
    survived: list[str] = []

    def bad(_: dict) -> None:
        raise RuntimeError("boom")

    def good(_: dict) -> None:
        survived.append("ok")

    events.register("evt", bad)
    events.register("evt", good)
    events.dispatch("evt", {})
    assert survived == ["ok"]
