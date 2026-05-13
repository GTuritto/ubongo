"""Constrained shell execution. Phase 11 v0.1 safety contract.

Anything that runs a process on the user's machine MUST go through
`run_constrained`. The contract is intentionally narrow:
  - explicit command allowlist (read-mostly: ls, cat, grep, git, pytest, ...)
  - no shell metacharacters (no pipes, redirects, command substitution)
  - no path traversal arguments
  - `shell=False` always
  - PATH restricted to a small set of standard bin dirs
  - cwd is the repo root
  - 10s default timeout

Phase 15 will harden this with filesystem allowlists, env scrubbing beyond
PATH, and (where feasible on macOS+Linux) seccomp / chroot. Phase 11's job
is to establish the seam: one module owns the entire contract.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("ubongo.sandbox")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "ls", "pwd", "echo", "cat", "head", "tail", "wc", "grep", "find",
    "git", "python", "python3", "pip", "uv", "pytest", "sqlite3",
    "true", "false",
})

_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"
_DEFAULT_TIMEOUT_SECONDS = 10
_STDOUT_CAP = 2048
_STDERR_CAP = 1024

# Tokens that must never appear inside a single argument. shlex.split already
# handles quoting; this catches things that survived quoting.
_BAD_METACHARS: tuple[str, ...] = (";", "|", "&", "`", "$(", ">", "<")

# Forbidden path fragments (defense in depth on argument-side traversal /
# accessing sensitive files). Phase 15 will replace with a positive allowlist.
_BAD_PATH_FRAGMENTS: tuple[str, ...] = ("..", "/etc", "/var", "/usr/local/var")


class SandboxRefused(Exception):
    """Raised when a command is rejected before execution."""


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    latency_ms: int
    argv: tuple[str, ...]


def _check_metachars(argv: list[str]) -> None:
    for token in argv:
        for meta in _BAD_METACHARS:
            if meta in token:
                raise SandboxRefused(f"shell metacharacter {meta!r} rejected")


def _check_paths(argv: list[str]) -> None:
    for token in argv[1:]:  # skip program name
        if token.startswith("~"):
            raise SandboxRefused("home-dir traversal '~' rejected")
        for frag in _BAD_PATH_FRAGMENTS:
            if frag in token:
                raise SandboxRefused(f"path fragment {frag!r} rejected in argument {token!r}")


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n…(truncated; {len(text) - cap} more bytes)"


def run_constrained(cmd: str, *, timeout: int | None = None) -> SandboxResult:
    """Run a single allowlisted command inside the repo root with a tight env.

    Raises SandboxRefused for any command that fails the safety checks.
    Returns a SandboxResult for every actual subprocess execution (including
    failures and timeouts).
    """
    timeout_value = timeout if timeout is not None else _DEFAULT_TIMEOUT_SECONDS

    if not cmd or not cmd.strip():
        raise SandboxRefused("empty command")

    # Reject shell metachars BEFORE shlex sees them; shlex is happy with `;`
    # as a literal token, which is exactly the injection we want to block.
    _check_metachars([cmd])

    try:
        argv = shlex.split(cmd, posix=True)
    except ValueError as exc:
        raise SandboxRefused(f"unparseable command: {exc}") from None
    if not argv:
        raise SandboxRefused("empty command")

    program = argv[0]
    if program not in ALLOWED_COMMANDS:
        raise SandboxRefused(f"program {program!r} not in allowlist")

    _check_metachars(argv)
    _check_paths(argv)

    env = {"PATH": _SAFE_PATH, "HOME": str(_REPO_ROOT), "LC_ALL": "C", "LANG": "C"}
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            env=env,
            cwd=str(_REPO_ROOT),
            timeout=timeout_value,
            capture_output=True,
            text=True,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info(
            "sandbox_run",
            extra={"argv": argv, "exit_code": proc.returncode, "latency_ms": elapsed},
        )
        return SandboxResult(
            stdout=_truncate(proc.stdout, _STDOUT_CAP),
            stderr=_truncate(proc.stderr, _STDERR_CAP),
            exit_code=proc.returncode,
            latency_ms=elapsed,
            argv=tuple(argv),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.warning("sandbox_timeout", extra={"argv": argv, "latency_ms": elapsed})
        return SandboxResult(
            stdout=_truncate(exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), _STDOUT_CAP),
            stderr="(timed out)",
            exit_code=-1,
            latency_ms=elapsed,
            argv=tuple(argv),
        )
    except FileNotFoundError:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.warning("sandbox_not_installed", extra={"argv": argv})
        return SandboxResult(
            stdout="",
            stderr=f"(not installed: {program})",
            exit_code=-1,
            latency_ms=elapsed,
            argv=tuple(argv),
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.warning("sandbox_error", extra={"argv": argv, "cause": str(exc)})
        return SandboxResult(
            stdout="",
            stderr=str(exc)[:200],
            exit_code=-1,
            latency_ms=elapsed,
            argv=tuple(argv),
        )
