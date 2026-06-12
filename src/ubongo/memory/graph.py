"""Vault-link graph traversal (Phase 20e).

A thin read API over `vault_links` (populated from `[[wikilinks]]` in daily
notes by `memory/vault.py`). Paths are vault-relative strings (e.g.
`daily/2026-06-04.md`) for sources and the bare link target for destinations.
"""

from __future__ import annotations

from ubongo.memory import index_state


def outbound(path: str) -> list[str]:
    """Targets this note links to."""
    return index_state.vault_links_from(path)


def backlinks(path: str) -> list[str]:
    """Notes that link to this one."""
    return index_state.vault_links_to(path)


def neighbors(path: str) -> list[str]:
    """Both directions: outbound targets + inbound sources, de-duplicated and
    sorted."""
    seen = set(outbound(path)) | set(backlinks(path))
    return sorted(seen)


def traverse(path: str, depth: int = 1) -> list[str]:
    """Breadth-first set of notes reachable within `depth` hops along links (in
    either direction), excluding the start node. Bounded by `depth` so a dense
    graph can't run away."""
    if depth <= 0:
        return []
    visited: set[str] = {path}
    frontier = {path}
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            for n in neighbors(node):
                if n not in visited:
                    nxt.add(n)
        if not nxt:
            break
        visited |= nxt
        frontier = nxt
    visited.discard(path)
    return sorted(visited)
