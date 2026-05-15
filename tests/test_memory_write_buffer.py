from __future__ import annotations

import pytest

from ubongo.memory.write_buffer import WriteBuffer, workflow_buffer


def test_commit_writes_staged_callables_in_order():
    log: list[str] = []
    buf = WriteBuffer()
    buf.stage(lambda: log.append("a") or "first")
    buf.stage(lambda: log.append("b") or "second")
    buf.stage(lambda: log.append("c") or "third")
    out = buf.commit()
    assert log == ["a", "b", "c"]
    assert out == ["first", "second", "third"]
    assert buf.committed is True


def test_drop_does_not_write():
    log: list[str] = []
    buf = WriteBuffer()
    buf.stage(lambda: log.append("a"))
    buf.stage(lambda: log.append("b"))
    buf.drop()
    assert log == []
    assert buf.dropped is True
    assert buf.committed is False


def test_double_commit_raises():
    buf = WriteBuffer()
    buf.stage(lambda: None)
    buf.commit()
    with pytest.raises(RuntimeError, match="already committed"):
        buf.commit()


def test_stage_after_commit_raises():
    buf = WriteBuffer()
    buf.commit()  # empty buffer, no-op
    with pytest.raises(RuntimeError, match="already committed"):
        buf.stage(lambda: None)


def test_stage_after_drop_raises():
    buf = WriteBuffer()
    buf.drop()
    with pytest.raises(RuntimeError, match="already dropped"):
        buf.stage(lambda: None)


def test_drop_after_commit_raises():
    buf = WriteBuffer()
    buf.commit()
    with pytest.raises(RuntimeError, match="already committed"):
        buf.drop()


def test_commit_after_drop_raises():
    buf = WriteBuffer()
    buf.drop()
    with pytest.raises(RuntimeError, match="already dropped"):
        buf.commit()


def test_double_drop_raises():
    buf = WriteBuffer()
    buf.drop()
    with pytest.raises(RuntimeError, match="already dropped"):
        buf.drop()


def test_workflow_buffer_implicit_drops_when_neither_called():
    log: list[str] = []
    with workflow_buffer() as buf:
        buf.stage(lambda: log.append("never run"))
        # don't call commit or drop
    # On context exit, implicit drop should fire.
    assert log == []
    assert buf.dropped is True


def test_workflow_buffer_explicit_commit_runs_staged():
    log: list[str] = []
    with workflow_buffer() as buf:
        buf.stage(lambda: log.append("ran"))
        buf.commit()
    assert log == ["ran"]
    assert buf.committed is True


def test_workflow_buffer_explicit_drop_skips_staged():
    log: list[str] = []
    with workflow_buffer() as buf:
        buf.stage(lambda: log.append("should not run"))
        buf.drop()
    assert log == []
    assert buf.dropped is True


def test_commit_returns_callable_results():
    buf = WriteBuffer()
    buf.stage(lambda: 42, description="forty_two")
    buf.stage(lambda: "hello", description="greeting")
    out = buf.commit()
    assert out == [42, "hello"]


def test_staged_count_property():
    buf = WriteBuffer()
    assert buf.staged_count == 0
    buf.stage(lambda: None)
    buf.stage(lambda: None)
    assert buf.staged_count == 2
