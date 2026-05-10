from __future__ import annotations

import logging

from ubongo import events
from ubongo.memory import compaction  # noqa: F401  -- registers after_recall handler
from ubongo.memory import vault  # noqa: F401  -- registers after_send handler

logger = logging.getLogger("ubongo.memory")


def _after_llm_seam(payload: dict) -> None:
    """Phase-4 placeholder. Phase 8's wider after_llm payload will let this
    do real memory writes from a single subscription point. For now memory
    writes happen inline in handle_text; this handler logs the seam exists.
    """
    logger.debug("memory_after_llm_seam", extra={"payload_keys": sorted(payload.keys())})


events.register("after_llm", _after_llm_seam)
