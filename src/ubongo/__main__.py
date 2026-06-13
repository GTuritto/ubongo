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
    # Candidate 13: the MCP server channel. stdio unless --http (LAN posture).
    mcp_cmd = subparsers.add_parser(
        "mcp", help="Run the MCP server (stdio; --http serves the LAN)"
    )
    mcp_cmd.add_argument(
        "--http", action="store_true",
        help="Serve streamable HTTP instead of stdio (home-LAN only; no auth)",
    )
    mcp_cmd.add_argument(
        "--port", type=int,
        default=int(os.environ.get("UBONGO_MCP_PORT", "8765")),
        help="HTTP port (default 8765, or UBONGO_MCP_PORT)",
    )
    mcp_cmd.add_argument(
        "--addr", default=os.environ.get("UBONGO_MCP_ADDR", "0.0.0.0"),
        help="HTTP bind address (default 0.0.0.0, or UBONGO_MCP_ADDR)",
    )
    # v0.5 phase 03: the cross-channel approval surface. A turn gated in any
    # channel persists a record; these resolve it without the original channel.
    subparsers.add_parser("pending", help="List require_approval turns awaiting a decision")
    approve = subparsers.add_parser("approve", help="Approve a pending turn by decision id")
    approve.add_argument("decision_id", type=int)
    decline = subparsers.add_parser("decline", help="Decline a pending turn by decision id")
    decline.add_argument("decision_id", type=int)
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

    if args.command == "mcp":
        # Lazy import: the SDK is an optional extra; the core never loads it.
        try:
            from ubongo.mcp import server as mcp_server
        except ImportError:
            print("The MCP dependency is not installed.", file=sys.stderr)
            print(
                "Install it with:  ./install.sh --mcp   (or: uv sync --extra mcp)",
                file=sys.stderr,
            )
            return 1
        return mcp_server.run(http=args.http, port=args.port, addr=args.addr)

    if args.command == "pending":
        return oneshot.list_pending()
    if args.command == "approve":
        return oneshot.resolve_pending(args.decision_id, approve=True)
    if args.command == "decline":
        return oneshot.resolve_pending(args.decision_id, approve=False)

    startup_profile = profiling.resolve_startup_profile(
        getattr(args, "profile", None), os.environ.get("UBONGO_PROFILE")
    )
    if args.command == "send":
        return oneshot.run(args.message, args.persona, profile=startup_profile)
    return repl.run(startup_profile=startup_profile)


if __name__ == "__main__":
    sys.exit(main())
