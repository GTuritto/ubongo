from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("ubongo.events")

Handler = Callable[[dict[str, Any]], None]

_handlers: dict[str, list[Handler]] = defaultdict(list)


def register(event: str, handler: Handler) -> None:
    _handlers[event].append(handler)


def unregister(event: str, handler: Handler) -> None:
    handlers = _handlers.get(event, [])
    try:
        handlers.remove(handler)
    except ValueError:
        pass


def dispatch(event: str, payload: dict[str, Any]) -> int:
    """Run every handler registered for `event`. Returns the number of
    handlers that raised (caught + logged here). Callers that need durable
    side-effect semantics inspect the return value to decide whether to mark
    work as completed."""
    failures = 0
    for handler in _handlers.get(event, []):
        try:
            handler(payload)
        except Exception as exc:
            failures += 1
            logger.warning(
                "event_handler_error",
                extra={"event": event, "handler": getattr(handler, "__name__", repr(handler)), "error": str(exc)},
            )
    return failures


def clear() -> None:
    _handlers.clear()
