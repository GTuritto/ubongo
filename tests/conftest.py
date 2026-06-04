import os

# Phase 20: keep the suite offline + fast. Message writes call
# embeddings.index_message best-effort; this off-switch makes every embedding
# path a no-op so no test makes an embedding network call. Embedding-specific
# tests re-enable explicitly (monkeypatch embeddings.enabled / embeddings._cfg).
os.environ.setdefault("UBONGO_DISABLE_EMBEDDINGS", "1")
