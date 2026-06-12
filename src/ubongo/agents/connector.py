"""Connector Agent — Ubongo's door to external MCP servers (candidate 20).

The ninth worker (`composer=False`): given the turn's message, it discovers
the tools the configured MCP servers offer (Compendium et al.), asks its model
to plan zero or more tool calls as JSON, executes the plan through the client
session layer, and returns the results as a Finding for downstream agents to
compose from. It writes nothing durable (single-writer rule), and the
governance matrix treats any workflow containing it as irreversible
(ADR-0016) — external calls happened.

Failure shapes are honest, never fatal: no SDK / no enabled servers / no
applicable tool each produce an `ok=True` finding saying exactly that (the
persona answers unaided); a plan that cannot be parsed or whose every call
failed produces `ok=False, error="connector_mcp_error"` so the Repair ladder
applies (peer replacement: architect).
"""

from __future__ import annotations

import json
import logging

from ubongo.agents.base import AgentInput, AgentResult
from ubongo.agents.llm_run import run_agent_llm
from ubongo.config import load_config
from ubongo.context import build_system_prompt
from ubongo.llm import complete
from ubongo.memory import vault

logger = logging.getLogger("ubongo.agents.connector")

_DEFAULT_MAX_TOKENS = 800
_MAX_CALLS_PER_TURN = 4
_MAX_RESULT_CHARS = 4000


def _parse_plan(text: str) -> "list[dict] | None":
    """Tolerant JSON plan parse (Evaluator-style): accepts a bare object or a
    fenced block; returns the calls list, [] for an explicit empty plan, or
    None when no plan can be recovered."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0] if "```" in raw else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        plan = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    calls = plan.get("calls")
    if not isinstance(calls, list):
        return None
    cleaned = []
    for c in calls[:_MAX_CALLS_PER_TURN]:
        if isinstance(c, dict) and c.get("server") and c.get("tool"):
            cleaned.append({
                "server": str(c["server"]), "tool": str(c["tool"]),
                "arguments": c.get("arguments") if isinstance(c.get("arguments"), dict) else {},
            })
    return cleaned


def _catalog_text(catalog) -> str:
    lines = []
    for t in catalog:
        schema = json.dumps(t.input_schema.get("properties", {}), default=str)
        lines.append(f"- server={t.server} tool={t.name}: {t.description}\n  arguments: {schema}")
    return "\n".join(lines)


class ConnectorAgent:
    name = "connector"
    role = "external capability over MCP: plans and executes tool calls on configured servers"

    def __init__(self) -> None:
        cfg = load_config()
        models = cfg.get("models", {})
        self.default_model = models.get("connector") or models.get("default", "")
        self.max_tokens = int(
            cfg.get("agents", {}).get("connector", {}).get("max_tokens", _DEFAULT_MAX_TOKENS)
        )

    def run(self, input: AgentInput, context) -> AgentResult:
        from ubongo.mcp import client

        if not client.sdk_available():
            return AgentResult(
                text="[Connector] The MCP extra is not installed "
                     "(./install.sh --mcp or uv sync --extra mcp); no external tools available.",
                ok=True, model=None, tokens_in=0, tokens_out=0, latency_ms=0,
            )
        if not client.servers():
            return AgentResult(
                text="[Connector] No MCP servers are enabled in settings (mcp.servers); "
                     "no external tools available.",
                ok=True, model=None, tokens_in=0, tokens_out=0, latency_ms=0,
            )
        catalog = client.tool_catalog()
        if not catalog:
            return AgentResult(
                text="[Connector] The enabled MCP servers answered with no tools "
                     "(or could not be reached); proceeding without external data.",
                ok=True, model=None, tokens_in=0, tokens_out=0, latency_ms=0,
            )

        system_prompt = (
            build_system_prompt("operator", agent_role=self.role)
            + "\n\nYou are the Connector Agent. Below is the catalog of external MCP "
            "tools available this turn. Decide which calls (0 to "
            f"{_MAX_CALLS_PER_TURN}) would genuinely help answer the user's message, "
            "and reply with ONLY a JSON object: "
            '{"calls": [{"server": "...", "tool": "...", "arguments": {...}}], '
            '"reason": "..."}. Use {"calls": []} when no tool applies.'
            + "\n\n## Tool catalog\n\n" + _catalog_text(catalog)
        )
        prompt_hint = input.directives.repair_prompt_hint
        if prompt_hint:
            system_prompt += "\n\n## Repair guidance\n\n" + prompt_hint

        def on_success(completion):
            calls = _parse_plan(completion.text)
            if calls is None:
                logger.warning("connector_plan_unparseable")
                return AgentResult(
                    text="", ok=False, model=completion.model,
                    tokens_in=completion.tokens_in, tokens_out=completion.tokens_out,
                    latency_ms=completion.latency_ms, error="connector_mcp_error",
                )
            if not calls:
                return AgentResult(
                    text="[Connector] No external tool applies to this message.",
                    ok=True, model=completion.model,
                    tokens_in=completion.tokens_in, tokens_out=completion.tokens_out,
                    latency_ms=completion.latency_ms,
                    metadata={"mcp_calls": []},
                )
            results = [client.call_tool(c["server"], c["tool"], c["arguments"])
                       for c in calls]
            blocks, call_log = [], []
            for r in results:
                call_log.append({"server": r.server, "tool": r.tool, "ok": r.ok})
                if r.ok:
                    blocks.append(f"## {r.server} / {r.tool}\n\n{r.text[:_MAX_RESULT_CHARS]}")
                else:
                    blocks.append(f"## {r.server} / {r.tool} — FAILED\n\n{r.error}")
            try:  # best-effort audit, never blocks the turn
                vault.append_audit_entry(
                    "mcp",
                    f"connector called {len(results)} tool(s): "
                    + ", ".join(f"{c['server']}/{c['tool']}({'ok' if c['ok'] else 'failed'})"
                                for c in call_log),
                )
            except Exception:
                logger.warning("connector_audit_failed", exc_info=True)
            if not any(r.ok for r in results):
                return AgentResult(
                    text="\n\n".join(blocks), ok=False, model=completion.model,
                    tokens_in=completion.tokens_in, tokens_out=completion.tokens_out,
                    latency_ms=completion.latency_ms, error="connector_mcp_error",
                    metadata={"mcp_calls": call_log},
                )
            return AgentResult(
                text="[Connector] External tool results:\n\n" + "\n\n".join(blocks),
                ok=True, model=completion.model,
                tokens_in=completion.tokens_in, tokens_out=completion.tokens_out,
                latency_ms=completion.latency_ms,
                metadata={"mcp_calls": call_log},
            )

        return run_agent_llm(
            agent_name="connector",
            logger=logger,
            input=input,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": input.message}],
            default_model=self.default_model,
            default_max_tokens=self.max_tokens,
            complete_fn=complete,
            log_extra={"catalog_tools": len(catalog)},
            on_success=on_success,
        )
