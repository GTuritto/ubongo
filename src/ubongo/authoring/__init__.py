"""Self-authored skills (the self-extension experiment).

Ubongo's GP loop (`ubongo.evolution`) *tunes* what already exists. This package
lets it *author brand-new skills*: draft a `config/skills/<name>/` folder
(SKILL.md + prompt templates), optionally carrying a constrained-bash command
template built only from the already-allowlisted programs in `ubongo.sandbox`.

The lifecycle mirrors the GP loop deliberately (draft -> quarantine -> evaluate
-> human approval -> live), and reuses its mechanisms. The safety spine:

1. Every candidate is validated against the exact `skills._parse_skill` schema
   and, if it carries a command template, against `sandbox.validate_command`.
2. Any command-bearing candidate is forced to risk>=medium / irreversible,
   regardless of what the drafting model declared.
3. Drafts live in `config/skills_candidates/` (which `skills.py` does NOT scan),
   so nothing is discoverable until the user approves it.

Phase 1 ships the candidate model, drafting, validation, quarantine, and the
manual `/author` + `/skill-candidates` listing. Evaluation (Phase 2), the
approval gate (Phase 3), and the autonomous daemon (Phase 4) build on top.
"""

from __future__ import annotations
