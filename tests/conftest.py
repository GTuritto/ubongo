import os

# Phase 20: keep the suite offline + fast. Message writes call
# embeddings.index_message best-effort; this off-switch makes every embedding
# path a no-op so no test makes an embedding network call. Embedding-specific
# tests re-enable explicitly (monkeypatch embeddings.enabled / embeddings._cfg).
os.environ.setdefault("UBONGO_DISABLE_EMBEDDINGS", "1")

# Phase 21: never start the vault watcher daemon during the suite.
os.environ.setdefault("UBONGO_DISABLE_VAULT_WATCH", "1")

# Authoring Phase 2: candidate evaluation makes LLM calls; keep it a no-op by
# default so the suite stays offline. Evaluation-specific tests re-enable it
# explicitly (delete the env var) and patch authoring.sandbox.complete.
os.environ.setdefault("UBONGO_DISABLE_AUTHORING_EVAL", "1")

# Authoring Phase 4: never start the autonomous authoring daemon during the suite.
os.environ.setdefault("UBONGO_DISABLE_AUTHORING", "1")

# v0.5 phase 06: never start the standing-jobs daemon during the suite (the
# runner/loop are tested directly with master.handle mocked).
os.environ.setdefault("UBONGO_DISABLE_JOBS", "1")
