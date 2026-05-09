from __future__ import annotations

import argparse
import logging
import sys

from ubongo.config import ConfigError, load_config
from ubongo.logging import log_startup, setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ubongo", description="Ubongo CLI")
    subparsers = parser.add_subparsers(dest="command")
    send = subparsers.add_parser("send", help="Send a one-shot message (no-op until Phase 1)")
    send.add_argument("message", help="Message text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    setup_logging(config["logging"]["level"])
    log_startup(config)

    if args.command == "send":
        logging.getLogger("ubongo").info(
            "cli_send_received",
            extra={"length": len(args.message), "phase": "0e_no_op"},
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
