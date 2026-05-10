from __future__ import annotations

import os
from datetime import date, time
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo.memory import vault  # noqa: E402


@pytest.fixture
def vault_root(tmp_path: Path):
    vault.set_vault_root(tmp_path)
    yield tmp_path
    vault.set_vault_root(None)


def test_first_write_creates_file_with_frontmatter_and_h1(vault_root: Path) -> None:
    path = vault.append_to_daily_note(
        date(2026, 5, 10),
        time(14, 25, 30),
        "hi",
        "hello",
        "casual",
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "date: 2026-05-10" in text
    assert "tags: [ubongo, daily]" in text
    assert "# 2026-05-10\n" in text
    assert "## 14:25:30 — casual" in text
    assert "**You:**" in text
    assert "**Ubongo:**" in text
    assert "hi" in text
    assert "hello" in text


def test_second_write_appends_without_duplicating_header(vault_root: Path) -> None:
    d = date(2026, 5, 10)
    vault.append_to_daily_note(d, time(14, 25, 30), "first", "1", "casual")
    vault.append_to_daily_note(d, time(14, 30, 0), "second", "2", "architect")
    text = vault.daily_note_path(d).read_text(encoding="utf-8")
    assert text.count("---\n") == 2  # opening + closing of frontmatter, only once
    assert text.count(f"# {d.isoformat()}\n") == 1
    assert text.count("## 14:25:30 — casual") == 1
    assert text.count("## 14:30:00 — architect") == 1


def test_auto_routed_suffix_present_only_when_flag_set(vault_root: Path) -> None:
    d = date(2026, 5, 10)
    vault.append_to_daily_note(d, time(14, 0, 0), "manual", "m", "casual", auto_routed=False)
    vault.append_to_daily_note(d, time(14, 1, 0), "auto", "a", "casual", auto_routed=True)
    text = vault.daily_note_path(d).read_text(encoding="utf-8")
    assert "## 14:00:00 — casual\n" in text
    assert "## 14:01:00 — casual (auto)" in text


def test_date_rollover_writes_to_separate_files(vault_root: Path) -> None:
    p1 = vault.append_to_daily_note(date(2026, 5, 10), time(23, 59, 0), "today", "t", "casual")
    p2 = vault.append_to_daily_note(date(2026, 5, 11), time(0, 1, 0), "tomorrow", "tm", "casual")
    assert p1 != p2
    assert p1.exists()
    assert p2.exists()
    assert "today" in p1.read_text(encoding="utf-8")
    assert "tomorrow" in p2.read_text(encoding="utf-8")


def test_user_markdown_is_preserved_verbatim(vault_root: Path) -> None:
    user_msg = "I have a question:\n\n```python\nprint('hi')\n```\n\nWhat do you think?"
    response = "**Bold response.**"
    vault.append_to_daily_note(
        date(2026, 5, 10), time(14, 0, 0),
        user_msg, response, "architect",
    )
    text = vault.daily_note_path(date(2026, 5, 10)).read_text(encoding="utf-8")
    assert "```python" in text
    assert "print('hi')" in text
    assert "**Bold response.**" in text


def test_lazy_mkdir_for_daily_subdir(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    vault.set_vault_root(fresh)
    try:
        assert not fresh.exists()
        vault.append_to_daily_note(date(2026, 5, 10), time(0, 0, 0), "x", "y", "casual")
        assert (fresh / "daily" / "2026-05-10.md").exists()
    finally:
        vault.set_vault_root(None)
