"""Backup + portability (v0.5 phase 07): a portable, secret-free archive of an
instance (DB + vault + config), and a restore that re-arms grants on a new
envelope. Root/target are parameterized so tests use tmp dirs, not the repo."""

from __future__ import annotations

import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import backup  # noqa: E402


def _make_instance(root: Path, *, with_grant: bool = False) -> None:
    """A fake instance tree: data/ubongo.db, vault/, config/, and a .env that
    must NOT be backed up."""
    (root / "data").mkdir(parents=True)
    (root / "vault" / "daily").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / ".env").write_text("OPENROUTER_API_KEY=secret\n")
    (root / "vault" / "daily" / "2026-06-20.md").write_text("# note\n")
    (root / "config" / "settings.yaml").write_text("models: {}\n")
    db = root / "data" / "ubongo.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE grants (id INTEGER PRIMARY KEY, capability_class TEXT, "
                 "status TEXT, revoked_at TIMESTAMP)")
    if with_grant:
        conn.execute("INSERT INTO grants (capability_class, status) VALUES ('connector:news', 'active')")
    conn.commit()
    conn.close()
    # A disposable profiler dump that must NOT ride along.
    (root / "data" / "profiles").mkdir()
    (root / "data" / "profiles" / "turn.prof").write_text("x")


def test_backup_includes_data_config_vault_and_excludes_secrets(tmp_path):
    root = tmp_path / "inst"
    _make_instance(root)
    archive = backup.create_backup(tmp_path / "out", root=root,
                                   now_iso_fn=lambda: "2026-06-20T12:00:00.000Z")
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert any(n.endswith("data/ubongo.db") for n in names)
    assert any("config/settings.yaml" in n for n in names)
    assert any("vault/daily/2026-06-20.md" in n for n in names)
    assert not any(n.endswith(".env") for n in names)          # secret-free
    assert not any("profiles" in n for n in names)             # no disposable dumps


def test_backup_to_explicit_archive_path(tmp_path):
    root = tmp_path / "inst"
    _make_instance(root)
    target = tmp_path / "mybackup.tar.gz"
    archive = backup.create_backup(target, root=root, now_iso_fn=lambda: "t")
    assert archive == target and target.exists()


def test_restore_reproduces_and_rearms_grants(tmp_path):
    root = tmp_path / "inst"
    _make_instance(root, with_grant=True)
    archive = backup.create_backup(tmp_path / "out", root=root, now_iso_fn=lambda: "t")
    target = tmp_path / "restored"
    revoked = backup.restore_backup(archive, target)          # fresh_grants default True
    assert (target / "data" / "ubongo.db").exists()
    assert (target / "config" / "settings.yaml").exists()
    assert revoked == 1                                       # the active grant re-armed
    status = sqlite3.connect(target / "data" / "ubongo.db").execute(
        "SELECT status FROM grants WHERE capability_class='connector:news'").fetchone()[0]
    assert status == "revoked"


def test_restore_keep_grants(tmp_path):
    root = tmp_path / "inst"
    _make_instance(root, with_grant=True)
    archive = backup.create_backup(tmp_path / "out", root=root, now_iso_fn=lambda: "t")
    target = tmp_path / "restored"
    revoked = backup.restore_backup(archive, target, fresh_grants=False)
    assert revoked == 0
    status = sqlite3.connect(target / "data" / "ubongo.db").execute(
        "SELECT status FROM grants").fetchone()[0]
    assert status == "active"                                 # preserved


def test_restore_rejects_path_traversal(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("pwn")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escape.txt")
    with pytest.raises(ValueError):
        backup.restore_backup(evil, tmp_path / "target")
