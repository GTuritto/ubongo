from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("ubongo.events")

Handler = Callable[[dict[str, Any]], None]

_handlers: dict[str, list[Handler]] = defaultdict(list)


def register(event: str, handler: Handler) -> None:
    _handlers[event].append(handler)


def dispatch(event: str, payload: dict[str, Any]) -> None:
    for handler in _handlers.get(event, []):
        try:
            handler(payload)
        except Exception as exc:
            logger.warning(
                "event_handler_error",
                extra={"event": event, "handler": getattr(handler, "__name__", repr(handler)), "error": str(exc)},
            )


def clear() -> None:
    _handlers.clear()
