"""WriteBuffer: stage-then-commit semantics for durable memory writes.

Phase 13d formalizes the existing "only commit if the workflow succeeded"
rule that master.handle was relying on by accident (the assistant-message
commit was gated by `if result.ok`). The buffer makes the contract
explicit: writes are staged during a workflow turn and executed exactly
once on commit; on drop, nothing is written.

v0.1 covers one staged writer — `MemoryAgent.commit_assistant_turn` from
master.handle. The vault `after_send` projection is already gated on
`result.ok` via the Phase-7 queue (after_send_payload is None when
ok=False, so flush_delivered doesn't fire after_send). When Phase 19/20
agents stage further mid-flight writes (research-discovered facts,
vault-link suggestions), they reuse the same `buf.stage(callable)`
interface — no per-agent rollback to reinvent.

The MemoryAgent single-writer rule is unchanged: staged callables still
go through `commit_assistant_turn` / `memory_writer()`, just with the
execution-ordering guarantee that the buffer provides.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("ubongo.memory.write_buffer")


class WriteBuffer:
    """Stage durable-memory writes; commit or drop atomically.

    State machine: pending → committed (writes executed) | dropped (writes
    discarded). Re-entry into commit / drop / stage after either terminal
    state raises RuntimeError to surface contract violations during tests.

    Returns from each staged callable are collected and returned from
    `commit()` so master.handle can read e.g. `assistant_message_id`.
    """

    def __init__(self) -> None:
        self._staged: list[tuple[Callable[[], Any], str]] = []
        self._committed: bool = False
        self._dropped: bool = False

    @property
    def staged_count(self) -> int:
        return len(self._staged)

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def dropped(self) -> bool:
        return self._dropped

    def stage(self, write: Callable[[], Any], *, description: str = "") -> None:
        if self._committed:
            raise RuntimeError("WriteBuffer already committed; cannot stage")
        if self._dropped:
            raise RuntimeError("WriteBuffer already dropped; cannot stage")
        self._staged.append((write, description))

    def commit(self) -> list[Any]:
        """Execute every staged callable in order. Returns the list of
        return values. After commit, the buffer is terminal."""
        if self._committed:
            raise RuntimeError("WriteBuffer already committed")
        if self._dropped:
            raise RuntimeError("WriteBuffer already dropped; cannot commit")
        results: list[Any] = []
        for write, description in self._staged:
            results.append(write())
        logger.debug(
            "write_buffer_committed",
            extra={"count": len(self._staged)},
        )
        self._committed = True
        return results

    def drop(self) -> None:
        """Discard every staged callable without executing it. After drop,
        the buffer is terminal."""
        if self._committed:
            raise RuntimeError("WriteBuffer already committed; cannot drop")
        if self._dropped:
            raise RuntimeError("WriteBuffer already dropped")
        logger.debug(
            "write_buffer_dropped",
            extra={"count": len(self._staged)},
        )
        self._staged.clear()
        self._dropped = True


@contextmanager
def workflow_buffer():
    """Context manager that yields a fresh WriteBuffer for one turn.

    If the caller forgets to commit or drop before exiting the block,
    drop wins — better to leak a not-yet-persisted message than to flush
    a half-finished workflow's state."""
    buf = WriteBuffer()
    try:
        yield buf
    finally:
        if not buf.committed and not buf.dropped:
            logger.warning(
                "write_buffer_implicit_drop",
                extra={"staged_count": buf.staged_count},
            )
            buf.drop()
