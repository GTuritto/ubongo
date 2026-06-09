"""The quarantine area for drafted skills (Phase 1d).

A drafted, validated candidate is written to `config/skills_candidates/<name>/`
as a real SKILL.md + prompts/ folder, but that directory is NOT the one
`skills.py` scans, so a quarantined skill is never discoverable by the
classifier or `/skills`. Promotion (Phase 3) materializes the folder into
`config/skills/<name>/`; until then it is inert markdown plus an `authored_skills`
row recording its status.

`set_candidates_dir` mirrors `skills.set_skills_dir` as a test hook.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ubongo.authoring.candidate import SkillCandidate
from ubongo.memory import store

logger = logging.getLogger("ubongo.authoring.quarantine")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_CANDIDATES_DIR = _REPO_ROOT / "config" / "skills_candidates"

_candidates_dir: Path = _DEFAULT_CANDIDATES_DIR


def set_candidates_dir(path: Path | None) -> None:
    """Override the quarantine directory (test hook). None resets to default."""
    global _candidates_dir
    _candidates_dir = Path(path) if path is not None else _DEFAULT_CANDIDATES_DIR


def candidates_dir() -> Path:
    return _candidates_dir


def _render_skill_md(candidate: SkillCandidate) -> str:
    """Build a SKILL.md (frontmatter + body) from a candidate. Prompt keys map to
    `prompts/<key>.md` so the materialized folder is a valid skill folder."""
    frontmatter: dict = {
        "name": candidate.name,
        "description": candidate.description,
        "risk": candidate.risk,
        "reversibility": candidate.reversibility,
        "default_persona": candidate.default_persona,
    }
    if candidate.prompts:
        frontmatter["prompts"] = {key: f"prompts/{key}.md" for key in candidate.prompts}
    fm = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).rstrip()
    body = candidate.body.rstrip()
    parts = [f"---\n{fm}\n---\n\n{body}\n"]
    if candidate.is_command_skill:
        # Record the command shape in the body so a human reviewing the
        # quarantined folder sees exactly what would run.
        parts.append(f"\n## Constrained command\n\n```sh\n{candidate.command_template.strip()}\n```\n")
    return "".join(parts)


def write_candidate_folder(candidate: SkillCandidate, *, base: Path | None = None) -> Path:
    """Write the candidate's SKILL.md + prompts/ under `<base>/<name>/`.

    Defaults to the quarantine dir. Overwrites an existing folder of the same
    name in that base (a re-draft supersedes its predecessor in quarantine).
    Returns the skill folder path.
    """
    root = (base or _candidates_dir) / candidate.name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(_render_skill_md(candidate), encoding="utf-8")
    if candidate.prompts:
        prompts_dir = root / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        for key, content in candidate.prompts.items():
            (prompts_dir / f"{key}.md").write_text(content.rstrip() + "\n", encoding="utf-8")
    logger.info("authoring_quarantined", extra={"name": candidate.name, "path": str(root)})
    return root


def persist(candidate: SkillCandidate, *, source: str = "manual") -> int:
    """Quarantine a validated candidate: write its folder and record a draft row.

    Returns the new `authored_skills` id. The generation increments per name so
    re-authoring an existing skill is tracked as a new generation.
    """
    folder = write_candidate_folder(candidate)
    generation = store.max_authored_generation(candidate.name) + 1
    row_id = store.append_authored_skill(
        name=candidate.name,
        description=candidate.description,
        status="draft",
        generation=generation,
        source=source,
        candidate=candidate.to_dict(),
        quarantine_path=str(folder),
    )
    logger.info(
        "authoring_persisted",
        extra={"id": row_id, "name": candidate.name, "generation": generation},
    )
    return row_id
