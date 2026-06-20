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
class AgentDirectives:
    """Typed control signals the orchestrator passes down to an agent.

    The directive seam (Phase 06): what the runner/Master may tell an agent to
    do for one run. Every field is optional; the default is "no directive."
    Replaces the old untyped `metadata` string keys read across the agents, so a
    misspelled directive fails at construction instead of silently no-op'ing.

    Distinct from `AgentInput.metadata`, which remains an open dict for the
    Memory agent's commit payload (conversation_id, response_text, ...).
    """

    override_model: str | None = None
    max_tokens_override: int | None = None
    repair_prompt_hint: str | None = None
    debate_role: str | None = None
    skill: str | None = None
    exec_command: str | None = None
    # v0.5 phase 07: the resolved verbosity level for this turn (terse|deep);
    # None / "normal" is a no-op. The composer persona appends one length line.
    verbosity: str | None = None


@dataclass(frozen=True)
class AgentInput:
    message: str
    history: tuple[dict, ...]
    summary_text: str | None
    prior_findings: tuple[str, ...]
    directives: AgentDirectives = field(default_factory=AgentDirectives)
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
