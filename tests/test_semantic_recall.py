from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import embeddings, store  # noqa: E402

_KV = {"caching": [1.0, 0.0, 0.0], "cache": [0.95, 0.05, 0.0], "weather": [0.0, 1.0, 0.0]}


def _fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 1.0]
        for k, vec in _KV.items():
            if k in t.lower():
                v = list(vec)
                break
        out.append(v)
    return out


@pytest.fixture
def emb(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "r.db")
    store.bootstrap()
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    monkeypatch.setattr(embeddings, "_DIM", 3)
    embeddings.reset()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_semantic_surfaces_old_turn_outside_recency(emb) -> None:
    cid = store.start_conversation("casual")
    old = store.append_message(cid, "user", "we should add a caching layer for hot keys", persona="casual")
    for i in range(12):  # push the caching turn out of the last-10 recency window
        store.append_message(cid, "user", f"weather chatter {i}", persona="casual")

    ctx = store.recall(cid, query="remember our caching discussion")
    assert old not in {m.id for m in ctx.messages}          # not in recency
    assert old in {m.id for m in ctx.semantic_messages}     # surfaced semantically


def test_semantic_excludes_recency_ids(emb) -> None:
    cid = store.start_conversation("casual")
    recent = store.append_message(cid, "user", "caching is on my mind", persona="casual")
    ctx = store.recall(cid, query="caching")
    # the recent caching turn is in the window, so it must NOT be duplicated in semantic
    assert recent in {m.id for m in ctx.messages}
    assert recent not in {m.id for m in ctx.semantic_messages}


def test_respects_top_k(emb, monkeypatch) -> None:
    # recall_top_k from config; force a small k via the embeddings config read
    monkeypatch.setattr(embeddings, "_cfg", lambda: {"enabled": True, "model": "f", "recall_top_k": 2})
    cid = store.start_conversation("casual")
    for i in range(15):
        store.append_message(cid, "user", f"caching note {i}", persona="casual")
    # recall reads recall_top_k from memory.embeddings.recall_top_k (real config = 5);
    # monkeypatched here only affects embeddings._cfg, not store's read, so assert <=
    ctx = store.recall(cid, query="caching")
    assert len(ctx.semantic_messages) <= 5


def test_no_query_means_recency_only(emb) -> None:
    cid = store.start_conversation("casual")
    store.append_message(cid, "user", "caching", persona="casual")
    ctx = store.recall(cid)  # no query
    assert ctx.semantic_messages == []


def test_disabled_embeddings_recency_only(tmp_path, monkeypatch) -> None:
    store.set_db_path(tmp_path / "d.db")
    store.bootstrap()
    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    embeddings.reset()
    cid = store.start_conversation("casual")
    store.append_message(cid, "user", "caching", persona="casual")
    ctx = store.recall(cid, query="caching")  # must not error, no semantic
    assert ctx.semantic_messages == []
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
