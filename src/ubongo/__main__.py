from __future__ import annotations

import argparse
import os
import sys

from ubongo import oneshot, profiling, repl
from ubongo.config import ConfigError, load_config
from ubongo.logging import log_startup, setup_logging

# Candidate 12: one startup knob, shared by the REPL path and `send`. A bare
# --profile means cpu (backward compatible with the old store_true flag);
# --profile off overrides a UBONGO_PROFILE env var.
_PROFILE_FLAG_KWARGS = dict(
    nargs="?",
    const="cpu",
    choices=["cpu", "mem", "all", "off"],
    default=None,
    help="Start with the profiler armed: cpu (default), mem, all, or off "
         "(overrides UBONGO_PROFILE). CPU reports land in data/profiles/.",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ubongo", description="Ubongo CLI")
    parser.add_argument("--profile", **_PROFILE_FLAG_KWARGS)
    subparsers = parser.add_subparsers(dest="command")
    send = subparsers.add_parser("send", help="Run a single turn and exit")
    send.add_argument("message", help="Message text")
    send.add_argument(
        "--persona",
        default=None,
        help="Persona to use for this turn (architect, operator, casual)",
    )
    # SUPPRESS: without it the subparser's default (None) would clobber a
    # top-level `ubongo --profile mem send "hi"` in the shared namespace.
    send.add_argument(
        "--profile", **{**_PROFILE_FLAG_KWARGS, "default": argparse.SUPPRESS}
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

    startup_profile = profiling.resolve_startup_profile(
        getattr(args, "profile", None), os.environ.get("UBONGO_PROFILE")
    )
    if args.command == "send":
        return oneshot.run(args.message, args.persona, profile=startup_profile)
    return repl.run(startup_profile=startup_profile)


if __name__ == "__main__":
    sys.exit(main())
