"""Grant-check policy for the decision matrix (v0.5 phase 05).

The Connector plans its tool calls at execute time, so at governance-decision
time we know only the *enabled servers*, not the specific tools. So a capability
class is server-granular: `connector:<server>` (per-tool-name allowlists stay
deferred until a real integration gives concrete tool names — ADR-0016/0019).

This module answers two questions for `governance.decision.decide()`:
- which capability classes does this (connector) turn touch?
- are they all granted, or is this a first encounter that must ask?

It reads the grant registry (`memory.grant_state`) and the enabled-server config
(`mcp.client`); it makes no decision of its own and writes nothing.
"""

from __future__ import annotations


def is_connector_workflow(workflow) -> bool:
    return "connector" in (getattr(workflow, "agents", ()) or ())


def capability_classes(workflow) -> list[str]:
    """The capability classes a connector turn could touch — one per enabled
    MCP server. Empty for a non-connector turn or when no server is enabled
    (nothing to gate; the Connector degrades to an honest no-op finding)."""
    if not is_connector_workflow(workflow):
        return []
    try:
        from ubongo.mcp import client as _mcp_client
        return [f"connector:{s.name}" for s in _mcp_client.servers()]
    except Exception:
        return []


def ungranted_classes(workflow, *, scope: str = "*") -> list[str]:
    """The capability classes this turn touches that have no active grant —
    the first-encounter set. Empty means every class is already granted.

    Fail-closed: if the registry can't be read, every touched class counts as
    ungranted (ask rather than silently allow)."""
    classes = capability_classes(workflow)
    if not classes:
        return []
    from ubongo.memory import grant_state
    out = []
    for c in classes:
        try:
            granted = grant_state.is_granted(c, scope=scope)
        except Exception:
            granted = False  # unreadable registry → ask
        if not granted:
            out.append(c)
    return out


def grant_connector_turn(workflow, *, scope: str = "*", purpose: str | None = None) -> list[int]:
    """Persist active grants for every ungranted class this connector turn
    touches. Called when an approved connector turn proceeds. Returns the new
    grant ids (empty when nothing needed granting)."""
    from ubongo.memory import grant_state
    ids = []
    for c in ungranted_classes(workflow, scope=scope):
        ids.append(grant_state.grant(c, consequence_class="irreversible", scope=scope,
                                      purpose=purpose or "approved connector turn"))
    return ids
