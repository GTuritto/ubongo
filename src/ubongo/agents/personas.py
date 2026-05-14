from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.llm import LLMError, complete

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.personas")

_LLM_FAILURE_MESSAGE = "Sorry, I couldn't reach the model. Check the logs."

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _REPO_ROOT / "config" / "personas"


@dataclass(frozen=True)
class Persona:
    name: str
    body: str
    model: str
    max_tokens: int


_registry: dict[str, Persona] = {}


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + len("\n---\n"):].lstrip("\n")
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        raise ValueError(f"Persona frontmatter must be a YAML mapping, got {type(fm).__name__}")
    return fm, body


def _load(name: str) -> Persona:
    path = _PERSONAS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Persona file not found: {path}")
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    model_key = fm.get("default_model")
    max_tokens = fm.get("max_tokens")
    if not model_key:
        raise ValueError(f"Persona '{name}' frontmatter missing 'default_model'")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError(f"Persona '{name}' frontmatter missing or invalid 'max_tokens'")

    config = load_config()
    models = config.get("models", {})
    if model_key not in models:
        raise ValueError(
            f"Persona '{name}' references models.{model_key}, "
            f"but settings.yaml has no such entry"
        )
    return Persona(name=name, body=body.rstrip(), model=models[model_key], max_tokens=max_tokens)


def get(name: str) -> Persona:
    cached = _registry.get(name)
    if cached is not None:
        return cached
    persona = _load(name)
    _registry[name] = persona
    return persona


def reload() -> None:
    _registry.clear()


class BasePersonaAgent:
    """Concrete behavior shared by every persona agent.

    Subclasses set `_persona_name` as a class attribute; the constructor
    binds the registry name and loads the model/max_tokens from the
    persona file. composer=True marks these as the agents whose text
    becomes the user-facing WorkflowResult.text.
    """

    _persona_name: str = ""  # must be overridden
    role = "persona composer"
    composer = True

    def __init__(self) -> None:
        if not self._persona_name:
            raise TypeError(
                f"{type(self).__name__} must set _persona_name on the class"
            )
        self.persona_name = self._persona_name
        self.name = self._persona_name
        persona = get(self._persona_name)
        self.default_model = persona.model
        self._max_tokens = persona.max_tokens

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        t0 = time.monotonic()
        persona = get(self.persona_name)
        skill_name = input.metadata.get("skill")
        base = build_system_prompt(self.persona_name, skill=skill_name)

        sections: list[str] = [base]
        if input.summary_text:
            sections.append(f"## Conversation summary so far\n\n{input.summary_text}")
        if input.prior_findings:
            for i, finding in enumerate(input.prior_findings, start=1):
                label = "Research findings" if i == 1 else f"Prior agent findings #{i}"
                sections.append(f"## {label}\n\n{finding}")
        # Phase 12d: debate mode tags the second-and-onward speaker with
        # debate_role="challenge" so they argue against the prior position.
        # A debate_role="synthesize" tag is set on the synthesizer turn.
        debate_role = input.metadata.get("debate_role")
        if debate_role == "challenge":
            sections.append(
                "## Debate role: challenge\n\nYou are in a debate. Read the prior turns above "
                "and argue against the position they take. Find the load-bearing assumption "
                "they did not name; surface the most likely failure mode. Be specific; do not "
                "restate their points."
            )
        elif debate_role == "synthesize":
            sections.append(
                "## Debate role: synthesize\n\nYou are synthesizing a debate. Read the full "
                "transcript above and produce a single answer: state where the debaters agreed, "
                "where they did not, and the recommendation that survives the disagreement. "
                "Pick a side when the evidence supports one; name the residual risk."
            )
        system_prompt = "\n\n".join(sections)
        model = input.metadata.get("override_model") or persona.model

        try:
            completion = complete(
                system_prompt=system_prompt,
                messages=list(input.history),
                model=model,
                max_tokens=persona.max_tokens,
            )
        except LLMError as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.error(
                "persona_llm_error",
                extra={
                    "persona": self.persona_name,
                    "model": model,
                    "cause": str(exc.cause) if exc.cause else None,
                },
            )
            return AgentResult(
                text=_LLM_FAILURE_MESSAGE,
                ok=False,
                model=model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=elapsed,
                error="persona_llm_error",
            )

        logger.info(
            "persona_run",
            extra={
                "persona": self.persona_name,
                "model": completion.model,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "latency_ms": completion.latency_ms,
                "attempts": completion.attempts,
                "had_findings": bool(input.prior_findings),
            },
        )
        return AgentResult(
            text=completion.text,
            ok=True,
            model=completion.model,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            latency_ms=completion.latency_ms,
        )


class ArchitectPersona(BasePersonaAgent):
    _persona_name = "architect"


class OperatorPersona(BasePersonaAgent):
    _persona_name = "operator"


class CasualPersona(BasePersonaAgent):
    _persona_name = "casual"


VALID_PERSONAS: tuple[str, ...] = ("architect", "operator", "casual")
