"""Index state — projection/idempotency metadata for the memory indexers
(moved from store.py, v0.5 phase 02).

Owns embedding_meta (the text-hash sidecar that keeps re-embedding idempotent),
vault_links (the [[wikilink]] graph rows), and vault_state / vault_conflicts
(the watcher's echo-suppression hashes and conflict queue). Pure CRUD over
store.connection(); the indexers themselves live in memory/embeddings.py,
memory/graph.py, memory/vault.py and memory/vault_watch.py.
"""

from __future__ import annotations

from ubongo.memory.store import Message, _row_to_message, connection, now_iso

# embedding_meta is the idempotency sidecar for message vectors (the vectors
# live in vec_messages, created lazily by memory/embeddings.py). vault_links is
# the [[wikilink]] graph populated from daily notes.


def embedding_meta_hash(message_id: int) -> str | None:
    """The stored text hash for a message's embedding, or None if not indexed."""
    conn = connection()
    row = conn.execute(
        "SELECT text_hash FROM embedding_meta WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row["text_hash"] if row else None


def upsert_embedding_meta(message_id: int, text_hash: str) -> None:
    conn = connection()
    conn.execute(
        """
        INSERT INTO embedding_meta (message_id, text_hash, embedded_at) VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET text_hash = excluded.text_hash,
                                              embedded_at = excluded.embedded_at
        """,
        (message_id, text_hash, now_iso()),
    )


def messages_by_ids(ids: list[int]) -> list["Message"]:
    """Fetch messages by id, returned in ascending id order. Used to hydrate
    semantic-recall hits into Message rows."""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    conn = connection()
    rows = conn.execute(
        f"""
        SELECT id, conversation_id, role, content, timestamp, persona, model, tokens_in, tokens_out
        FROM messages WHERE id IN ({placeholders}) ORDER BY id ASC
        """,
        ids,
    ).fetchall()
    return [_row_to_message(r) for r in rows]


def upsert_vault_link(source_path: str, target_path: str, *, link_type: str = "wikilink") -> None:
    """Idempotent insert of a vault link (composite PK absorbs duplicates)."""
    conn = connection()
    conn.execute(
        """
        INSERT INTO vault_links (source_path, target_path, link_type, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_path, target_path, link_type) DO NOTHING
        """,
        (source_path, target_path, link_type, now_iso()),
    )


def vault_links_from(source_path: str) -> list[str]:
    """Outbound link targets from a note."""
    conn = connection()
    rows = conn.execute(
        "SELECT target_path FROM vault_links WHERE source_path = ? ORDER BY target_path",
        (source_path,),
    ).fetchall()
    return [r["target_path"] for r in rows]


def vault_links_to(target_path: str) -> list[str]:
    """Inbound sources linking to a note (backlinks)."""
    conn = connection()
    rows = conn.execute(
        "SELECT source_path FROM vault_links WHERE target_path = ? ORDER BY source_path",
        (target_path,),
    ).fetchall()
    return [r["source_path"] for r in rows]


# --- Vault sync: state + conflicts (Phase 21) -------------------------------
# vault_state records the hash of what the system last wrote to each note (echo
# suppression for the watcher). vault_conflicts queues edit/write collisions.


def record_vault_write(path: str, content_hash: str) -> None:
    """Record the hash the system just wrote to `path` (vault-relative)."""
    conn = connection()
    conn.execute(
        """
        INSERT INTO vault_state (path, content_hash, last_written_at) VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET content_hash = excluded.content_hash,
                                        last_written_at = excluded.last_written_at
        """,
        (path, content_hash, now_iso()),
    )


def get_vault_hash(path: str) -> str | None:
    conn = connection()
    row = conn.execute("SELECT content_hash FROM vault_state WHERE path = ?", (path,)).fetchone()
    return row["content_hash"] if row else None


def append_vault_conflict(*, path: str, system_hash: str | None, disk_hash: str | None) -> int:
    """Queue an edit/write collision; returns its id. De-dups: if an open
    conflict already exists for the path with the same disk_hash, reuse it."""
    conn = connection()
    existing = conn.execute(
        "SELECT id FROM vault_conflicts WHERE path = ? AND status = 'open' AND disk_hash IS ? LIMIT 1",
        (path, disk_hash),
    ).fetchone()
    if existing:
        return int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO vault_conflicts (path, detected_at, system_hash, disk_hash, status)
        VALUES (?, ?, ?, ?, 'open')
        """,
        (path, now_iso(), system_hash, disk_hash),
    )
    return int(cursor.lastrowid)


def open_vault_conflicts() -> list[dict]:
    conn = connection()
    rows = conn.execute(
        "SELECT id, path, detected_at, system_hash, disk_hash FROM vault_conflicts "
        "WHERE status = 'open' ORDER BY id"
    ).fetchall()
    return [
        {"id": r["id"], "path": r["path"], "detected_at": r["detected_at"],
         "system_hash": r["system_hash"], "disk_hash": r["disk_hash"]}
        for r in rows
    ]


def get_vault_conflict(conflict_id: int) -> dict | None:
    conn = connection()
    r = conn.execute(
        "SELECT id, path, detected_at, system_hash, disk_hash, status, resolution "
        "FROM vault_conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "path": r["path"], "detected_at": r["detected_at"],
            "system_hash": r["system_hash"], "disk_hash": r["disk_hash"],
            "status": r["status"], "resolution": r["resolution"]}


def resolve_vault_conflict(conflict_id: int, resolution: str) -> bool:
    """Mark an open conflict resolved with the chosen resolution. Returns True if
    a still-open row was updated."""
    conn = connection()
    cur = conn.execute(
        "UPDATE vault_conflicts SET status = 'resolved', resolution = ? "
        "WHERE id = ? AND status = 'open'",
        (resolution, conflict_id),
    )
    return cur.rowcount > 0
