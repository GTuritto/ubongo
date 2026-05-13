"""Agent protocol + per-run input/output dataclasses.

Phase 9 lands the seam every worker (Research, Memory, Persona, plus Phase 10
Evaluator/Critic and Phase 11 Coding/Execution/Repair) plugs into. The protocol
is intentionally minimal: a `run(input, context)` callable plus three string
attributes (`name`, `role`, `default_model`). The runner introspects nothing
else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ubongo.master import Context


@dataclass(frozen=True)
class AgentInput:
    message: str
    history: tuple[dict, ...]
    summary_text: str | None
    prior_findings: tuple[str, ...]
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    text: str
    ok: bool
    model: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    confidence: float | None = None
    metadata: dict = field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class Agent(Protocol):
    name: str
    role: str
    default_model: str

    def run(self, input: AgentInput, context: "Context") -> AgentResult: ...
