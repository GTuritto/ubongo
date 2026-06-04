from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import embeddings, store  # noqa: E402


# Deterministic 3-dim "embeddings" keyed by a keyword in the text.
_KV = {"cat": [1.0, 0.0, 0.0], "dog": [0.9, 0.1, 0.0], "car": [0.0, 1.0, 0.0]}


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
    store.set_db_path(tmp_path / "e.db")
    store.bootstrap()
    # Force embeddings on (conftest disables them by default) with a fake model.
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    monkeypatch.setattr(embeddings, "_DIM", 3)
    embeddings.reset()
    yield
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_vec_available_true_when_enabled(emb) -> None:
    assert embeddings.vec_available() is True


def test_vec_unavailable_when_disabled(tmp_path, monkeypatch) -> None:
    store.set_db_path(tmp_path / "d.db")
    store.bootstrap()
    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    embeddings.reset()
    assert embeddings.vec_available() is False
    assert embeddings.index_message(1, "hello") is False
    assert embeddings.search_messages("hello", 5) == []
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")


def test_index_and_search(emb) -> None:
    cid = store.start_conversation("casual")
    ids = {w: store.append_message(cid, "user", w, persona="casual") for w in ("cat", "dog", "car")}
    # append_message indexes on write; nearest to "cat" excluding cat -> dog before car
    hits = embeddings.search_messages("cat", 2, exclude_ids={ids["cat"]})
    assert [mid for mid, _ in hits] == [ids["dog"], ids["car"]]


def test_idempotent_reindex_skips_embed(emb, monkeypatch) -> None:
    cid = store.start_conversation("casual")
    mid = store.append_message(cid, "user", "cat", persona="casual")  # indexed on write
    calls = {"n": 0}

    def _counting_embed(texts):
        calls["n"] += 1
        return _fake_embed(texts)

    monkeypatch.setattr(embeddings, "embed", _counting_embed)
    assert embeddings.index_message(mid, "cat") is False  # unchanged -> no embed
    assert calls["n"] == 0
    assert embeddings.index_message(mid, "dog") is True   # changed -> re-embed
    assert calls["n"] == 1


def test_search_excludes_and_scopes(emb) -> None:
    c1 = store.start_conversation("casual")
    c2 = store.start_conversation("casual")
    a = store.append_message(c1, "user", "cat", persona="casual")
    b = store.append_message(c2, "user", "dog", persona="casual")  # other conversation
    hits = embeddings.search_messages("cat", 5, conversation_id=c1)
    assert a in [mid for mid, _ in hits]
    assert b not in [mid for mid, _ in hits]  # scoped out


def test_text_hash_stable() -> None:
    assert embeddings.text_hash("abc") == embeddings.text_hash("abc")
    assert embeddings.text_hash("abc") != embeddings.text_hash("abd")
