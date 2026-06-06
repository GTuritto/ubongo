from __future__ import annotations

from ubongo.agents.base import AgentResult
from ubongo.invoke import SequentialHarvest, resolve_agents


class _FakeAgent:
    def __init__(self, name: str, *, composer: bool = False) -> None:
        self.name = name
        self.composer = composer


def _res(ok=True, text="x", conf=None, tin=0, tout=0) -> AgentResult:
    return AgentResult(
        text=text, ok=ok, model="m",
        tokens_in=tin, tokens_out=tout, latency_ms=1, confidence=conf,
    )


# ---------- resolve_agents ----------

def test_resolve_skips_repair_and_drops_missing():
    a = _FakeAgent("architect")
    e = _FakeAgent("evaluator")
    registry = {"architect": a, "evaluator": e, "repair": _FakeAgent("repair")}
    resolved = resolve_agents(registry, ["architect", "repair", "ghost", "evaluator"])
    assert resolved == [("architect", a), ("evaluator", e)]


def test_resolve_preserves_order_and_duplicates():
    a = _FakeAgent("a")
    registry = {"a": a}
    assert resolve_agents(registry, ["a", "a"]) == [("a", a), ("a", a)]


def test_resolve_empty_when_nothing_registered():
    assert resolve_agents({}, ["x", "repair"]) == []


# ---------- SequentialHarvest ----------

def test_harvest_threads_prior_findings_in_order():
    h = SequentialHarvest()
    a = _FakeAgent("a")
    h.observe(a, _res(text="first"))
    assert h.prior == ("first",)
    h.observe(a, _res(text="second"))
    assert h.prior == ("first", "second")


def test_harvest_no_thread_when_disabled():
    h = SequentialHarvest(thread_prior=False)
    h.observe(_FakeAgent("a"), _res(text="x"))
    assert h.prior == ()


def test_harvest_skips_prior_for_failed_or_empty():
    h = SequentialHarvest()
    h.observe(_FakeAgent("a"), _res(ok=False, text="nope"))   # not ok -> skipped
    h.observe(_FakeAgent("b"), _res(ok=True, text=""))         # ok but empty -> skipped
    assert h.prior == ()
    assert h.any_failure is True


def test_harvest_picks_last_composer():
    h = SequentialHarvest()
    c1 = _FakeAgent("c1", composer=True)
    plain = _FakeAgent("p")
    c2 = _FakeAgent("c2", composer=True)
    r1, r2, r3 = _res(text="one"), _res(text="two"), _res(text="three")
    h.observe(c1, r1)
    h.observe(plain, r2)
    h.observe(c2, r3)
    out = h.outcome()
    assert out.last_composer is r3
    assert out.last_ok is r3
    assert out.composer_text == "three"


def test_harvest_carries_last_confidence():
    h = SequentialHarvest()
    h.observe(_FakeAgent("a"), _res(conf=0.4))
    h.observe(_FakeAgent("b"), _res(conf=0.8))
    h.observe(_FakeAgent("c"), _res(conf=None))  # None does not overwrite
    assert h.outcome().evaluator_confidence == 0.8


def test_harvest_sums_tokens_including_non_ok():
    h = SequentialHarvest()
    h.observe(_FakeAgent("a"), _res(tin=10, tout=20))
    h.observe(_FakeAgent("b"), _res(ok=False, tin=5, tout=0))
    assert h.outcome().total_tokens == 35


def test_composer_text_falls_back_to_last_prior_when_no_composer():
    h = SequentialHarvest()
    h.observe(_FakeAgent("a"), _res(text="alpha"))
    h.observe(_FakeAgent("b"), _res(text="beta"))
    out = h.outcome()
    assert out.last_composer is None
    assert out.composer_text == "beta"


def test_composer_with_empty_text_falls_back_to_prior():
    # A composer that produced no text falls through to the last finding,
    # matching the sandbox's `composer_text or (prior[-1] ...)` rule.
    h = SequentialHarvest()
    h.observe(_FakeAgent("p"), _res(text="finding"))
    h.observe(_FakeAgent("c", composer=True), _res(text=""))
    out = h.outcome()
    assert out.composer_text == "finding"


def test_mark_failure():
    h = SequentialHarvest()
    h.observe(_FakeAgent("a"), _res(ok=True))
    assert h.outcome().any_failure is False
    h.mark_failure()
    assert h.outcome().any_failure is True


def test_empty_harvest_outcome():
    out = SequentialHarvest().outcome()
    assert out.last_ok is None and out.last_composer is None
    assert out.any_failure is False and out.total_tokens == 0
    assert out.composer_text == ""
