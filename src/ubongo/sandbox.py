"""Constrained shell execution. v0.1 safety contract (Phase 11 + Phase 15c).

Anything that runs a process on the user's machine MUST go through
`run_constrained`. The contract is intentionally narrow:
  - explicit command allowlist (read-mostly: ls, cat, grep, git, pytest, ...)
  - no shell metacharacters (no pipes, redirects, command substitution)
  - no path traversal; absolute-path arguments must resolve inside the repo
  - `shell=False` always
  - the program is resolved to an absolute path by the parent; the child gets
    an EMPTY PATH, so it cannot spawn further programs by bare name
  - a tight env (PATH="", repo-root HOME, C locale) — nothing inherited
  - cwd is the repo root
  - 10s default timeout

Known v0.1 limitation: this is not OS-level isolation. Network is governed by
the allowlist (no curl/wget/ssh), not by seccomp / sandbox-exec; an allowlisted
`python` could still open a socket. See docs/SECURITY.md. The seam stays in
this one module so a future hardening pass has a single place to land.
"""

from __future__ import annotations

import logging
import shlex
import shutil
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

# The directories the parent searches to resolve an allowlisted command to an
# absolute path. The CHILD never sees this — it runs with PATH="".
_RESOLUTION_PATH = "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"
_DEFAULT_TIMEOUT_SECONDS = 10
_STDOUT_CAP = 2048
_STDERR_CAP = 1024

# Phase 15c: resolve each allowlisted command to an absolute path once, at
# import. run_constrained dispatches the absolute path as argv[0] and gives the
# child an empty PATH — so a child process cannot itself shell out by name.
# An allowlisted command that does not resolve here is reported "(not installed)".
_PROGRAM_PATHS: dict[str, str] = {
    name: resolved
    for name in ALLOWED_COMMANDS
    if (resolved := shutil.which(name, path=_RESOLUTION_PATH)) is not None
}

# Tokens that must never appear inside a single argument. shlex.split already
# handles quoting; this catches things that survived quoting.
_BAD_METACHARS: tuple[str, ...] = (";", "|", "&", "`", "$(", ">", "<")

# Forbidden path fragments — relative traversal and obvious sensitive trees.
# Phase 15c adds a positive rule on top: any absolute-path argument must
# resolve inside the repo (see _check_paths).
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
        # Phase 15c filesystem allowlist: an absolute-path argument must
        # resolve inside the repo tree. This catches absolute paths the
        # fragment blacklist does not know about (anything outside /etc, /var).
        # We also check the right side of an '=' to catch --arg=/path.
        path_str = token
        if "=" in token:
            _, right = token.split("=", 1)
            if right.startswith("/"):
                path_str = right
        if path_str.startswith("/"):
            resolved = Path(path_str).resolve()
            if resolved != _REPO_ROOT and _REPO_ROOT not in resolved.parents:
                raise SandboxRefused(
                    f"absolute path {path_str!r} resolves outside the repo sandbox"
                )


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n…(truncated; {len(text) - cap} more bytes)"


def validate_command(cmd: str) -> list[str]:
    """Run every pre-execution safety check and return the parsed argv.

    Raises SandboxRefused for any command that fails the allowlist /
    metacharacter / path-traversal checks. This is the exact gate
    `run_constrained` applies before spawning, factored out so callers that need
    to *validate without executing* (e.g. the skill-authoring layer vetting a
    generated command template) reuse the identical contract instead of
    re-implementing it. The enforcement stays in this one module (ADR-0005).
    """
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
    return argv


def run_constrained(cmd: str, *, timeout: int | None = None) -> SandboxResult:
    """Run a single allowlisted command inside the repo root with a tight env.

    Raises SandboxRefused for any command that fails the safety checks.
    Returns a SandboxResult for every actual subprocess execution (including
    failures and timeouts).
    """
    timeout_value = timeout if timeout is not None else _DEFAULT_TIMEOUT_SECONDS

    argv = validate_command(cmd)
    program = argv[0]

    # Phase 15c: dispatch the resolved absolute path; the child gets PATH=""
    # so it cannot spawn further programs by bare name.
    resolved = _PROGRAM_PATHS.get(program)
    if resolved is None:
        logger.warning("sandbox_not_installed", extra={"argv": argv})
        return SandboxResult(
            stdout="", stderr=f"(not installed: {program})",
            exit_code=-1, latency_ms=0, argv=tuple(argv),
        )
    exec_argv = [resolved, *argv[1:]]

    env = {"PATH": "", "HOME": str(_REPO_ROOT), "LC_ALL": "C", "LANG": "C"}
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            exec_argv,
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
