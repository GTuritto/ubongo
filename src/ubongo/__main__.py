from __future__ import annotations

import argparse
import sys

from ubongo import oneshot, repl
from ubongo.config import ConfigError, load_config
from ubongo.logging import log_startup, setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ubongo", description="Ubongo CLI")
    subparsers = parser.add_subparsers(dest="command")
    send = subparsers.add_parser("send", help="Run a single turn and exit")
    send.add_argument("message", help="Message text")
    send.add_argument(
        "--persona",
        default=None,
        help="Persona to use for this turn (architect, operator, casual)",
    )
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
        return oneshot.run(args.message, args.persona)
    return repl.run()


if __name__ == "__main__":
    sys.exit(main())
