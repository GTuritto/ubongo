from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

from ubongo import events, skills  # noqa: E402
from ubongo.authoring import promotion, quarantine  # noqa: E402
from ubongo.authoring.candidate import SkillCandidate  # noqa: E402
from ubongo.authoring.promotion import PromotionError  # noqa: E402
from ubongo.memory import authoring_state
from ubongo.memory import store, vault  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    store.set_db_path(tmp_path / "t.db")
    store.bootstrap()
    quarantine.set_candidates_dir(tmp_path / "cand")
    promotion.set_backups_dir(tmp_path / "backups")
    skills.set_skills_dir(tmp_path / "live")
    audits: list[str] = []
    monkeypatch.setattr(vault, "append_audit_entry", lambda cat, line, **k: audits.append((cat, line)))
    decisions: list[dict] = []
    events.register("authoring_decision", decisions.append)
    yield tmp_path, audits, decisions
    events.unregister("authoring_decision", decisions.append)
    store.set_db_path(store._REPO_ROOT / "data" / "ubongo.db")
    quarantine.set_candidates_dir(None)
    promotion.set_backups_dir(None)
    skills.set_skills_dir(None)


def _candidate(name="diff-notes", body="Summarize the diff.", command=None) -> SkillCandidate:
    return SkillCandidate(name=name, description="summarize a git diff", risk="low",
                          reversibility="reversible", default_persona="operator",
                          body=body, prompts={"draft": "Notes for {diff}"},
                          command_template=command)


# --- approve ----------------------------------------------------------------

def test_approve_registers_and_is_discoverable(env) -> None:
    _, audits, decisions = env
    cid = quarantine.persist(_candidate())
    assert not skills.has("diff-notes")
    r = promotion.approve(cid)
    assert r.name == "diff-notes" and not r.backed_up
    assert skills.has("diff-notes")
    assert "diff-notes" in [s.name for s in skills.list_skills()]
    assert authoring_state.get_authored_skill(cid)["status"] == "approved"
    assert authoring_state.get_authored_skill(cid)["decided_at"] is not None
    assert any(c[0] == "authoring" and "approved" in c[1] for c in audits)
    assert decisions and decisions[-1]["event"] == "approved"


def test_approve_over_existing_backs_up(env) -> None:
    tmp, _, _ = env
    promotion.approve(quarantine.persist(_candidate(body="v1 body")))
    v1 = (skills.skills_dir() / "diff-notes" / "SKILL.md").read_text(encoding="utf-8")

    cid2 = quarantine.persist(_candidate(body="v2 body improved"))
    r2 = promotion.approve(cid2)
    assert r2.backed_up and r2.backup_path is not None
    assert Path(r2.backup_path).is_dir()
    assert authoring_state.get_authored_skill(cid2)["backup_path"] == r2.backup_path
    # live is now v2
    assert "v2 body improved" in (skills.skills_dir() / "diff-notes" / "SKILL.md").read_text()
    # the backup is byte-for-byte v1
    assert (Path(r2.backup_path) / "SKILL.md").read_text(encoding="utf-8") == v1


def test_approve_reenforces_risk_floor(env) -> None:
    # A row whose stored candidate claims low risk but carries a command must be
    # forced to medium/irreversible at the boundary, not just at draft time.
    cand = {"name": "runner", "description": "d", "risk": "low",
            "reversibility": "reversible", "default_persona": None, "body": "b",
            "prompts": {}, "command_template": "git status", "metadata": {}}
    cid = authoring_state.append_authored_skill(name="runner", description="d", status="draft",
                                      generation=1, source="manual", candidate=cand)
    promotion.approve(cid)
    md = (skills.skills_dir() / "runner" / "SKILL.md").read_text(encoding="utf-8")
    assert "risk: medium" in md
    assert "reversibility: irreversible" in md


def test_approve_unknown_id_raises(env) -> None:
    with pytest.raises(PromotionError):
        promotion.approve(999)


def test_approve_non_draft_raises(env) -> None:
    cid = quarantine.persist(_candidate())
    promotion.approve(cid)
    with pytest.raises(PromotionError):  # already approved
        promotion.approve(cid)


# --- reject -----------------------------------------------------------------

def test_reject_leaves_quarantined(env) -> None:
    _, _, decisions = env
    cid = quarantine.persist(_candidate())
    r = promotion.reject(cid)
    assert r.name == "diff-notes"
    assert authoring_state.get_authored_skill(cid)["status"] == "rejected"
    assert not skills.has("diff-notes")
    # quarantine folder is left in place
    assert (quarantine.candidates_dir() / "diff-notes" / "SKILL.md").exists()
    assert decisions[-1]["event"] == "rejected"


# --- rollback ---------------------------------------------------------------

def test_rollback_restores_prior_version(env) -> None:
    promotion.approve(quarantine.persist(_candidate(body="v1 body")))
    v1 = (skills.skills_dir() / "diff-notes" / "SKILL.md").read_text(encoding="utf-8")
    cid2 = quarantine.persist(_candidate(body="v2 body"))
    promotion.approve(cid2)

    r = promotion.rollback("diff-notes")
    assert r.restored
    assert skills.has("diff-notes")  # still registered, just the old version
    assert (skills.skills_dir() / "diff-notes" / "SKILL.md").read_text(encoding="utf-8") == v1
    assert authoring_state.get_authored_skill(cid2)["status"] == "rolled_back"


def test_rollback_without_backup_unregisters(env) -> None:
    cid = quarantine.persist(_candidate())
    promotion.approve(cid)
    r = promotion.rollback("diff-notes")
    assert not r.restored
    assert not skills.has("diff-notes")
    assert authoring_state.get_authored_skill(cid)["status"] == "rolled_back"


def test_rollback_nothing_live_raises(env) -> None:
    with pytest.raises(PromotionError):
        promotion.rollback("never-existed")


def test_repeated_rollback_steps_back_then_unregisters(env) -> None:
    promotion.approve(quarantine.persist(_candidate(body="v1")))
    promotion.approve(quarantine.persist(_candidate(body="v2")))
    promotion.approve(quarantine.persist(_candidate(body="v3")))
    promotion.rollback("diff-notes")  # v3 -> v2
    assert "v2" in (skills.skills_dir() / "diff-notes" / "SKILL.md").read_text()
    promotion.rollback("diff-notes")  # v2 -> v1
    assert "v1" in (skills.skills_dir() / "diff-notes" / "SKILL.md").read_text()
    promotion.rollback("diff-notes")  # v1 -> unregister
    assert not skills.has("diff-notes")
