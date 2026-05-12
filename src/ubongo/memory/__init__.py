from __future__ import annotations

import logging

from ubongo import events
from ubongo.memory import compaction  # noqa: F401  -- registers after_recall handler
from ubongo.memory import vault  # noqa: F401  -- vault module loaded
# Phase 9c: MemoryAgent owns the after_send vault projection (single-writer rule).
# Importing it here triggers the events.register call inside agents/memory.py.
from ubongo.agents import memory as _agents_memory  # noqa: F401

logger = logging.getLogger("ubongo.memory")


def _after_llm_seam(payload: dict) -> None:
    """Phase-4 placeholder. Phase 8's wider after_llm payload will let this
    do real memory writes from a single subscription point. For now memory
    writes happen inline in handle_text; this handler logs the seam exists.
    """
    logger.debug("memory_after_llm_seam", extra={"payload_keys": sorted(payload.keys())})


events.register("after_llm", _after_llm_seam)
