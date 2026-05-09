from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_STD_RECORD_KEYS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "event": str(record.msg),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


_SAFE_KEYS = {
    "models",
    "memory",
    "vault",
    "governance",
    "evolution",
    "logging",
}


def _redact(config: dict[str, Any]) -> dict[str, Any]:
    return {k: config[k] for k in _SAFE_KEYS if k in config}


def log_startup(config: dict[str, Any]) -> None:
    logger = logging.getLogger("ubongo")
    logger.info("startup", extra={"config": _redact(config)})
