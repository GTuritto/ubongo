"""Evaluator Agent: LLM-as-judge over the persona's response.

Phase 10 ships the first real feeder for the governance confidence signal.
The evaluator reads the user's question (input.message) and the last
producer's text (input.prior_findings[-1]) and returns a JSON object of
shape {"confidence": float, "issues": [str, ...]}. Confidence is clamped
to [0.0, 1.0]; up to 5 issues are kept.

The agent does not write to durable memory and does not produce a final
response (composer=False). Its score is harvested by the WorkflowRunner
onto WorkflowResult.evaluator_confidence and passed to governance.decide.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.llm import LLMError, complete

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.evaluator")

_DEFAULT_MAX_TOKENS = 400
_MAX_ISSUES = 5
_RAW_PREVIEW_LEN = 500
_CANDIDATE_TRUNCATE = 1024  # per-candidate cap for rank() prompt budget

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_code_fence(text: str) -> str:
    match = _CODE_FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text.strip()


def _parse_judgment(raw: str) -> tuple[float, list[str]] | None:
    """Parse the JSON judgment. Returns (confidence, issues) or None on failure."""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    conf_raw = data.get("confidence")
    try:
        conf = float(conf_raw)
    except (TypeError, ValueError):
        return None
    conf = max(0.0, min(1.0, conf))
    issues_raw = data.get("issues", []) or []
    if not isinstance(issues_raw, list):
        issues_raw = []
    issues = [str(x) for x in issues_raw[:_MAX_ISSUES]]
    return conf, issues


def _parse_ranking(raw: str, n_candidates: int) -> tuple[int, str, list[dict]] | None:
    """Phase 12b: parse the JSON ranking from rank(). Returns
    (winner_index, reason, scores) or None on failure."""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        winner_index = int(data.get("winner_index"))
    except (TypeError, ValueError):
        return None
    if winner_index < 0 or winner_index >= n_candidates:
        return None
    reason = str(data.get("reason") or "")
    scores_raw = data.get("scores") or []
    scores: list[dict] = []
    if isinstance(scores_raw, list):
        for entry in scores_raw[:n_candidates]:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index"))
                score = float(entry.get("score"))
            except (TypeError, ValueError):
                continue
            scores.append({
                "index": idx,
                "score": max(0.0, min(1.0, score)),
                "note": str(entry.get("note") or ""),
            })
    return winner_index, reason, scores


def _parse_agree(raw: str) -> bool | None:
    """Phase 12e: parse the JSON agreement from agree(). Returns True/False
    or None on failure."""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("agree")
    if isinstance(val, bool):
        return val
    return None


_JUDGE_RUBRIC = (
    "You are the Evaluator Agent. Judge the candidate response below against "
    "the user's question. Return ONLY a JSON object with this exact shape, no "
    "prose before or after:\n\n"
    '{"confidence": <float in [0.0, 1.0]>, "issues": [<short string>, ...]}\n\n'
    "Score rubric:\n"
    "- 0.9+ : answers the question directly, no hallucinated facts, complete.\n"
    "- 0.7-0.9 : answers correctly but with small gaps or unsupported claims.\n"
    "- 0.4-0.7 : partially answers; signals of hallucination or missing context.\n"
    "- 0.2-0.4 : largely wrong, misleading, or off-topic.\n"
    "- <0.2 : refuse-worthy: hallucinated, dangerous, or fundamentally broken.\n"
    "Keep `issues` to at most 5 short strings; empty list if none."
)

_RANK_RUBRIC = (
    "You are the Evaluator Agent acting as a comparative judge. Multiple "
    "candidate responses are listed below as `### Candidate <i> (<agent_name>)`. "
    "Pick the SINGLE best one for the user's question. Return ONLY a JSON object "
    "with this exact shape, no prose before or after:\n\n"
    '{"winner_index": <0-based int>, "reason": "<one short paragraph>", '
    '"scores": [{"index": 0, "score": 0.0..1.0, "note": "<one phrase>"}, ...]}\n\n'
    "Judging criteria: correctness, completeness, absence of hallucination, "
    "fit to the user's question. Phrasing and persona-voice differences do not "
    "count. If two candidates are equally good, pick the lower index."
)

_AGREE_RUBRIC = (
    "You are the Evaluator Agent. Two responses to the same user question are "
    "below. Decide whether they substantively AGREE on the answer. Different "
    "facts or different recommendations count as disagreement; phrasing or "
    "tone differences do not. Return ONLY a JSON object with this exact shape:\n\n"
    '{"agree": <true|false>, "reason": "<one phrase>"}'
)


class EvaluatorAgent:
    name = "evaluator"
    role = "LLM-as-judge: confidence, completeness, hallucination signals"
    composer = False

    def __init__(self) -> None:
        cfg = load_config()
        models = cfg.get("models", {})
        self.default_model = models.get("evaluator") or models.get("default", "")
        self.max_tokens = int(
            cfg.get("agents", {}).get("evaluator", {}).get("max_tokens", _DEFAULT_MAX_TOKENS)
        )

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        t0 = time.monotonic()
        candidate = input.prior_findings[-1] if input.prior_findings else ""
        if not candidate.strip():
            return AgentResult(
                text="",
                ok=False,
                model=self.default_model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                error="evaluator_no_candidate",
            )

        system_prompt = (
            build_system_prompt("operator", agent_role=self.role)
            + "\n\n" + _JUDGE_RUBRIC
            + "\n\n## User question\n\n" + input.message
            + "\n\n## Candidate response\n\n" + candidate
        )

        model = input.metadata.get("override_model") or self.default_model
        try:
            completion = complete(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": "Judge the candidate response."}],
                model=model,
                max_tokens=self.max_tokens,
            )
        except LLMError as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "evaluator_llm_error",
                extra={"model": model, "cause": str(exc.cause) if exc.cause else None},
            )
            return AgentResult(
                text="",
                ok=False,
                model=model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=elapsed,
                error="evaluator_llm_error",
            )

        parsed = _parse_judgment(completion.text)
        if parsed is None:
            logger.warning(
                "evaluator_parse_error",
                extra={"model": completion.model, "raw_preview": completion.text[:_RAW_PREVIEW_LEN]},
            )
            return AgentResult(
                text="",
                ok=False,
                model=completion.model,
                tokens_in=completion.tokens_in,
                tokens_out=completion.tokens_out,
                latency_ms=completion.latency_ms,
                error="evaluator_parse_error",
                metadata={"raw": completion.text[:_RAW_PREVIEW_LEN]},
            )

        conf, issues = parsed
        issues_str = "; ".join(issues) if issues else "none"
        logger.info(
            "evaluator_run",
            extra={
                "model": completion.model,
                "confidence": conf,
                "issue_count": len(issues),
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "latency_ms": completion.latency_ms,
            },
        )
        return AgentResult(
            text=f"Confidence: {conf:.2f}. Issues: {issues_str}.",
            ok=True,
            model=completion.model,
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
            latency_ms=completion.latency_ms,
            confidence=conf,
            metadata={"issues": issues, "raw": completion.text[:_RAW_PREVIEW_LEN]},
        )

    # ------------------------------------------------------------------
    # Phase 12 entry points: rank (competitive mode), agree (speculative mode)
    # ------------------------------------------------------------------

    def rank(
        self,
        message: str,
        candidates: list[tuple[str, str]],
        *,
        override_model: str | None = None,
    ) -> dict | None:
        """Phase 12b: pick the best candidate for `message` from a list of
        (agent_name, text) tuples.

        Returns:
          {"winner": <agent_name>, "winner_index": int, "reason": str,
           "scores": [{"index": int, "score": float, "note": str}, ...],
           "raw": str}
        or None on parse error / LLM failure / empty candidates list.

        Used by competitive mode in WorkflowRunner.
        """
        if not candidates:
            return None
        truncated = [
            (name, text if len(text) <= _CANDIDATE_TRUNCATE else text[:_CANDIDATE_TRUNCATE] + "…")
            for name, text in candidates
        ]
        candidate_block = "\n\n".join(
            f"### Candidate {i} ({name})\n\n{text}"
            for i, (name, text) in enumerate(truncated)
        )

        system_prompt = (
            build_system_prompt("operator", agent_role=self.role)
            + "\n\n" + _RANK_RUBRIC
            + "\n\n## User question\n\n" + message
            + "\n\n## Candidates\n\n" + candidate_block
        )
        model = override_model or self.default_model

        try:
            completion = complete(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": "Rank the candidates."}],
                model=model,
                max_tokens=self.max_tokens,
            )
        except LLMError as exc:
            logger.warning(
                "evaluator_rank_llm_error",
                extra={"model": model, "cause": str(exc.cause) if exc.cause else None},
            )
            return None

        parsed = _parse_ranking(completion.text, len(candidates))
        if parsed is None:
            logger.warning(
                "evaluator_rank_parse_error",
                extra={"model": completion.model, "raw_preview": completion.text[:_RAW_PREVIEW_LEN]},
            )
            return None
        winner_index, reason, scores = parsed
        winner_name = candidates[winner_index][0]
        logger.info(
            "evaluator_rank",
            extra={
                "model": completion.model,
                "winner": winner_name,
                "winner_index": winner_index,
                "candidates_count": len(candidates),
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
            },
        )
        return {
            "winner": winner_name,
            "winner_index": winner_index,
            "reason": reason,
            "scores": scores,
            "raw": completion.text[:_RAW_PREVIEW_LEN],
        }

    def agree(
        self,
        message: str,
        text_a: str,
        text_b: str,
        *,
        override_model: str | None = None,
    ) -> bool | None:
        """Phase 12e: do the two candidate texts substantively agree on the
        answer to `message`?

        Returns True / False / None (parse error or LLM failure).

        Used by speculative mode in WorkflowRunner to decide whether to append
        a correction block to the cheap response.
        """
        if not text_a.strip() or not text_b.strip():
            return None

        text_a_t = text_a if len(text_a) <= _CANDIDATE_TRUNCATE else text_a[:_CANDIDATE_TRUNCATE] + "…"
        text_b_t = text_b if len(text_b) <= _CANDIDATE_TRUNCATE else text_b[:_CANDIDATE_TRUNCATE] + "…"
        system_prompt = (
            build_system_prompt("operator", agent_role=self.role)
            + "\n\n" + _AGREE_RUBRIC
            + "\n\n## User question\n\n" + message
            + "\n\n## Response A\n\n" + text_a_t
            + "\n\n## Response B\n\n" + text_b_t
        )
        model = override_model or self.default_model

        try:
            completion = complete(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": "Do these responses substantively agree?"}],
                model=model,
                max_tokens=200,
            )
        except LLMError as exc:
            logger.warning(
                "evaluator_agree_llm_error",
                extra={"model": model, "cause": str(exc.cause) if exc.cause else None},
            )
            return None

        parsed = _parse_agree(completion.text)
        if parsed is None:
            logger.warning(
                "evaluator_agree_parse_error",
                extra={"model": completion.model, "raw_preview": completion.text[:_RAW_PREVIEW_LEN]},
            )
            return None
        logger.info(
            "evaluator_agree",
            extra={"model": completion.model, "agree": parsed},
        )
        return parsed
