"""Research Agent: retrieval + synthesis over conversation memory and vault.

Phase 9 retrieval is intentionally dumb: keyword overlap over recent
cross-conversation messages, grep over the most recent daily-note files.
Phase 20 swaps in sqlite-vec semantic recall and the vault-link graph.

The agent does not write to durable memory; it returns findings as
AgentResult.text and the persona agent composes the final reply.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.agents.llm_run import run_agent_llm
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.llm import complete
from ubongo.memory import store, vault

if TYPE_CHECKING:
    from ubongo.master import Context

logger = logging.getLogger("ubongo.agents.research")

_DEFAULT_MAX_TOKENS = 800
_MAX_CONV_MESSAGES = 30
_MAX_RETRIEVED_MESSAGES = 8
_MAX_VAULT_SNIPPETS = 5


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "for", "of",
    "to", "in", "on", "at", "by", "with", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "do", "does", "did", "this", "that",
    "these", "those", "it", "its", "we", "you", "i", "they", "he", "she",
    "what", "which", "who", "when", "where", "why", "how", "can", "could",
    "should", "would", "will", "shall", "may", "might", "must", "about",
    "into", "out", "over", "under", "again", "further", "than", "too",
    "very", "just", "also", "my", "me", "our", "your", "their", "his",
    "her",
})


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9_]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _filter_messages_by_overlap(query: str, messages: list) -> list:
    """Return messages sharing at least one content word with the query."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    hits: list = []
    for m in messages:
        if _tokens(m.content) & query_tokens:
            hits.append(m)
    return hits[-_MAX_RETRIEVED_MESSAGES:]


def _format_messages(messages: list) -> str:
    if not messages:
        return "(no relevant prior messages)"
    lines: list[str] = []
    for m in messages:
        role = m.role
        tag = f"[conv:{m.conversation_id}:msg:{m.id}]"
        snippet = m.content.replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        lines.append(f"- {tag} ({role}) {snippet}")
    return "\n".join(lines)


def _format_snippets(snippets: list) -> str:
    if not snippets:
        return "(no relevant vault snippets)"
    lines: list[str] = []
    for s in snippets:
        tag = f"[vault:{s.path}]"
        body = s.snippet.replace("\n", " ").strip()
        if len(body) > 240:
            body = body[:240] + "…"
        lines.append(f"- {tag} {body}")
    return "\n".join(lines)


class ResearchAgent:
    name = "research"
    role = "retrieval and synthesis over conversation memory and the vault"

    def __init__(self) -> None:
        cfg = load_config()
        models = cfg.get("models", {})
        self.default_model = models.get("research") or models.get("default", "")
        self.max_tokens = int(
            cfg.get("agents", {}).get("research", {}).get("max_tokens", _DEFAULT_MAX_TOKENS)
        )

    def run(self, input: AgentInput, context: "Context") -> AgentResult:
        all_recent = store.last_n_messages_global(_MAX_CONV_MESSAGES)
        relevant = _filter_messages_by_overlap(input.message, all_recent)
        snippets = vault.search_daily_notes(input.message, max_snippets=_MAX_VAULT_SNIPPETS)

        system_prompt = (
            build_system_prompt("operator", agent_role=self.role)
            + "\n\nYou are the Research Agent. Read the retrieved context below and produce "
            "a concise, neutral synthesis (max ~6 short paragraphs) of what is relevant to "
            "the user's question. Cite sources inline as [conv:<id>:msg:<id>] or "
            "[vault:<path>]. Do not answer in the user's voice; the persona agent will "
            "compose the final reply."
            + "\n\n## Retrieved conversation messages\n\n"
            + _format_messages(relevant)
            + "\n\n## Retrieved vault snippets\n\n"
            + _format_snippets(snippets)
        )

        # Phase 13b: Repair may pass a prompt-hint addendum on a same-model retry.
        prompt_hint = input.metadata.get("repair_prompt_hint")
        if prompt_hint:
            system_prompt = system_prompt + "\n\n## Repair guidance\n\n" + prompt_hint

        retrieval = {
            "retrieved_messages": len(relevant),
            "retrieved_snippets": len(snippets),
        }
        return run_agent_llm(
            agent_name="research",
            logger=logger,
            input=input,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": input.message}],
            default_model=self.default_model,
            default_max_tokens=self.max_tokens,
            complete_fn=complete,
            result_metadata=retrieval,
            success_log_extra=retrieval,
        )
