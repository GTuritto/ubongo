"""Semantic recall via sqlite-vec (Phase 20a).

Everything here is **best-effort and guarded**. `vec_available()` tries to load
the sqlite-vec extension and create the vec0 tables once per connection; if the
platform blocks extension loading or `memory.embeddings.enabled` is false it
returns False and every other entry point becomes a no-op. `embed()` returns
None on any failure. So a disabled / extension-blocked / endpoint-down
environment degrades cleanly to recency-only with no errors, and embedding work
never blocks a message commit.
"""

from __future__ import annotations

import hashlib
import logging
import os

from ubongo.config import load_config
from ubongo.memory import index_state
from ubongo.memory import store

logger = logging.getLogger("ubongo.memory.embeddings")

# text-embedding-3-small. Asserted on first index; a mismatch disables vec.
_DIM = 1536

# Cached vec readiness for the current connection; cleared by reset() (which
# store.set_db_path calls when the DB changes).
_vec_ready: bool | None = None


def _cfg() -> dict:
    return load_config().get("memory", {}).get("embeddings", {}) or {}


def enabled() -> bool:
    # A hard off-switch for the test suite (and any offline run): set
    # UBONGO_DISABLE_EMBEDDINGS to keep every embedding path a no-op without
    # touching config. The pytest conftest sets it so the suite never makes
    # embedding network calls; embedding tests opt back in.
    if os.environ.get("UBONGO_DISABLE_EMBEDDINGS"):
        return False
    return bool(_cfg().get("enabled", False))


def reset() -> None:
    """Forget the cached vec readiness (on DB swap or config change)."""
    global _vec_ready
    _vec_ready = None


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def embed(texts: list[str]) -> list[list[float]] | None:
    """Batch-embed via the configured model. None on disabled/empty/any failure."""
    if not texts or not enabled():
        return None
    model = _cfg().get("model")
    try:
        import litellm

        resp = litellm.embedding(model=model, input=texts)
        return [row["embedding"] for row in resp.data]
    except Exception as exc:  # network / auth / provider — best effort
        logger.warning("embed_failed", extra={"error": str(exc)[:160]})
        return None


def vec_available() -> bool:
    """True if sqlite-vec is loaded and the vec tables exist for the current
    connection. Cached. False when embeddings are disabled or the extension
    can't load (-> recency-only)."""
    global _vec_ready
    if _vec_ready is not None:
        return _vec_ready
    if not enabled():
        _vec_ready = False
        return False
    try:
        import sqlite_vec

        conn = store.connection()
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_messages USING vec0(embedding float[{_DIM}])")
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_vault USING vec0(embedding float[{_DIM}])")
        _vec_ready = True
    except Exception as exc:
        logger.warning("vec_unavailable", extra={"error": str(exc)[:160]})
        _vec_ready = False
    return _vec_ready


def index_message(message_id: int, text: str) -> bool:
    """Embed + store a message vector idempotently. Returns True if (re)indexed,
    False if unchanged or unavailable. Skips the embed call when the text hash
    is unchanged (scenario 2). Never raises."""
    if not text.strip() or not vec_available():
        return False
    h = text_hash(text)
    if index_state.embedding_meta_hash(message_id) == h:
        return False  # unchanged -> no embed call
    vecs = embed([text])
    if not vecs:
        return False
    try:
        import sqlite_vec

        conn = store.connection()
        conn.execute("DELETE FROM vec_messages WHERE rowid = ?", (message_id,))
        conn.execute(
            "INSERT INTO vec_messages(rowid, embedding) VALUES (?, ?)",
            (message_id, sqlite_vec.serialize_float32(vecs[0])),
        )
        index_state.upsert_embedding_meta(message_id, h)
        return True
    except Exception as exc:
        logger.warning("index_message_failed", extra={"message_id": message_id, "error": str(exc)[:160]})
        return False


def _vault_rowid(path: str) -> int:
    """A stable integer rowid for a vault path (vec0 tables key by rowid).
    60-bit path hash — collisions are negligible for a personal vault."""
    return int(hashlib.sha256(path.encode("utf-8")).hexdigest()[:15], 16)


def index_vault(path: str, text: str) -> bool:
    """Embed a vault note's text into vec_vault (Phase 21 ingest). Best-effort,
    no-op when unavailable; the watcher only calls it on change, so it re-embeds
    unconditionally."""
    if not text.strip() or not vec_available():
        return False
    vecs = embed([text])
    if not vecs:
        return False
    try:
        import sqlite_vec

        conn = store.connection()
        rid = _vault_rowid(path)
        conn.execute("DELETE FROM vec_vault WHERE rowid = ?", (rid,))
        conn.execute(
            "INSERT INTO vec_vault(rowid, embedding) VALUES (?, ?)",
            (rid, sqlite_vec.serialize_float32(vecs[0])),
        )
        return True
    except Exception as exc:
        logger.warning("index_vault_failed", extra={"path": path, "error": str(exc)[:160]})
        return False


def search_messages(
    query: str,
    k: int,
    *,
    exclude_ids: set[int] | None = None,
    conversation_id: int | None = None,
) -> list[tuple[int, float]]:
    """Top-k nearest message ids to `query` as (message_id, distance), best
    first, excluding `exclude_ids` and (when given) scoped to a conversation.
    Empty list when unavailable / k<=0 / embed fails. Never raises."""
    if k <= 0 or not query.strip() or not vec_available():
        return []
    vecs = embed([query])
    if not vecs:
        return []
    exclude = exclude_ids or set()
    # Over-fetch so post-filtering (excluded ids, conversation scope) still
    # yields up to k results.
    over = k + len(exclude) + 16
    try:
        import sqlite_vec

        conn = store.connection()
        rows = conn.execute(
            "SELECT rowid, distance FROM vec_messages WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (sqlite_vec.serialize_float32(vecs[0]), over),
        ).fetchall()
    except Exception as exc:
        logger.warning("search_failed", extra={"error": str(exc)[:160]})
        return []

    out: list[tuple[int, float]] = []
    for row in rows:
        mid = int(row["rowid"])
        if mid in exclude:
            continue
        if conversation_id is not None:
            owner = store.connection().execute(
                "SELECT conversation_id FROM messages WHERE id = ?", (mid,)
            ).fetchone()
            if owner is None or owner["conversation_id"] != conversation_id:
                continue
        out.append((mid, float(row["distance"])))
        if len(out) >= k:
            break
    return out
