"""Execution Agent: bridge from a target command to sandbox.run_constrained.

Phase 11 ships the agent as composer=False (output is data the persona
summarizes, not the user-facing response). Source of the command, in
order of precedence:
  1. input.directives.exec_command  (set by Master / /exec)
  2. a single fenced ```sh / ```bash code block in input.message
  3. otherwise: AgentResult(ok=False, error="execution_no_command")

Sandbox rejection is reported as ok=False with error="execution_refused".
Successful run returns formatted text (truncated stdout/stderr + exit code)
so the next agent can quote it; ok mirrors exit_code == 0.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from ubongo import sandbox
from ubongo.agents.base import AgentInput, AgentResult

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.execution")

_FENCED_BLOCK_RE = re.compile(
    r"```(?:sh|bash)\s*\n(.+?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_fenced_command(message: str) -> str | None:
    matches = _FENCED_BLOCK_RE.findall(message)
    if len(matches) != 1:
        return None
    body = matches[0].strip()
    # Multi-line bodies are not safe to collapse; v0.1 accepts only single-line.
    if "\n" in body:
        return None
    return body


def _format_result(result: sandbox.SandboxResult) -> str:
    cmd = " ".join(result.argv)
    return (
        f"$ {cmd}\n"
        f"exit={result.exit_code}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


class ExecutionAgent:
    name = "execution"
    role = "runs shell scripts via constrained-bash skill"
    composer = False
    default_model = ""

    def __init__(self, *, timeout: int | None = None) -> None:
        self.timeout = timeout

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        t0 = time.monotonic()
        cmd = input.directives.exec_command
        if not cmd:
            cmd = _extract_fenced_command(input.message)
        if not cmd:
            return AgentResult(
                text="",
                ok=False,
                model=None,
                tokens_in=0,
                tokens_out=0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error="execution_no_command",
            )

        try:
            sb_result = sandbox.run_constrained(cmd, timeout=self.timeout)
        except sandbox.SandboxRefused as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.info("execution_refused", extra={"cmd": cmd, "reason": str(exc)})
            return AgentResult(
                text=f"Refused: {exc}",
                ok=False,
                model=None,
                tokens_in=0,
                tokens_out=0,
                latency_ms=elapsed,
                error="execution_refused",
                metadata={"reason": str(exc), "cmd": cmd},
            )

        elapsed = int((time.monotonic() - t0) * 1000)
        formatted = _format_result(sb_result)
        logger.info(
            "execution_run",
            extra={
                "argv": list(sb_result.argv),
                "exit_code": sb_result.exit_code,
                "latency_ms": sb_result.latency_ms,
            },
        )
        return AgentResult(
            text=formatted,
            ok=(sb_result.exit_code == 0),
            model=None,
            tokens_in=0,
            tokens_out=0,
            latency_ms=elapsed,
            metadata={
                "exit_code": sb_result.exit_code,
                "argv": list(sb_result.argv),
                "sandbox_latency_ms": sb_result.latency_ms,
            },
        )
