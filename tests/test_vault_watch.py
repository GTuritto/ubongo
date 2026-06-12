from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import index_state
from ubongo.memory import embeddings, store, vault, vault_watch  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "w.db")
    store.bootstrap()
    vault.set_vault_root(tmp_path / "vault")
    # enable embeddings with a fake embed (no network)
    monkeypatch.setattr(embeddings, "enabled", lambda: True)
    monkeypatch.setattr(embeddings, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(embeddings, "_DIM", 3)
    embeddings.reset()
    yield tmp_path
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    vault.set_vault_root(None)


def _write_note(text="hi") -> Path:
    return vault.append_to_daily_note(datetime.date(2026, 6, 4), datetime.time(9, 0), "u", text, "casual")


def test_system_write_records_vault_state(env) -> None:
    note = _write_note()
    rel = vault.vault_relpath(note)
    assert index_state.get_vault_hash(rel) == vault.file_hash(note)


def test_scan_skips_own_write(env) -> None:
    _write_note()
    assert vault_watch.scan_once() == {"ingested": 0, "conflicts": 0}


def test_scan_ingests_external_edit_and_queues_conflict(env) -> None:
    note = _write_note()
    note.write_text(note.read_text() + "\nuser added this\n")  # external edit
    r = vault_watch.scan_once()
    assert r["ingested"] == 1
    assert r["conflicts"] == 1
    assert len(index_state.open_vault_conflicts()) == 1
    # vault_state advanced; a re-scan does not re-ingest
    assert vault_watch.scan_once()["ingested"] == 0


def test_new_external_file_ingested_without_conflict(env) -> None:
    # a note the system never wrote (user created it) -> ingest, no conflict
    daily = (env / "vault" / "daily")
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "2026-06-05.md").write_text("a brand new user note")
    r = vault_watch.scan_once()
    assert r["ingested"] == 1
    assert r["conflicts"] == 0


def test_watcher_off_by_default(env, monkeypatch) -> None:
    # config default vault.sync.enabled is false; start() returns False
    monkeypatch.delenv("UBONGO_DISABLE_VAULT_WATCH", raising=False)
    w = vault_watch.VaultWatcher(tick_seconds=0.01)
    assert w.start() is False
    assert w._thread is None


def test_watcher_start_stop_when_enabled(env, monkeypatch) -> None:
    monkeypatch.delenv("UBONGO_DISABLE_VAULT_WATCH", raising=False)
    monkeypatch.setattr(vault_watch, "enabled", lambda: True)
    w = vault_watch.VaultWatcher(tick_seconds=0.01)
    assert w.start() is True
    assert w._thread is not None and w._thread.is_alive()
    w.stop(timeout=2.0)
    assert not w._thread.is_alive()
