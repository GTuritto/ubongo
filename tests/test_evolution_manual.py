from __future__ import annotations

from pathlib import Path

import pytest

from ubongo.evolution import manual
from ubongo.evolution.targets import UnknownTargetError
from ubongo.memory import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path):
    store.set_db_path(tmp_path / "ubongo.db")
    store.bootstrap()
    yield
    store.set_db_path(None)


def test_generate_variants_unknown_target_raises():
    with pytest.raises(UnknownTargetError):
        manual.generate_variants("persona:nope")


def test_score_latest_generation_unknown_target_raises():
    with pytest.raises(UnknownTargetError):
        manual.score_latest_generation("routing:nope")


def test_score_latest_generation_no_variants_raises():
    # A valid target with no generated variants yet -> NoVariantsError, so the
    # /evaluate handler can show "run /optimize first".
    with pytest.raises(manual.NoVariantsError):
        manual.score_latest_generation("persona:architect")
