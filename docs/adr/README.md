# Architecture Decision Records

Load-bearing decisions for Ubongo v0.1, in [Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions). Each records the context, the decision, and its consequences. ADRs 0001–0009 were **backfilled** on 2026-06-04 from the build (`UBONGO_BUILD.md`), the per-phase plans in `Plans/`, the `STATUS.md` changelog, and commit history; 0010–0011 cover the Tier-6 decisions. **v0.1 is complete** (all 22 phases, 0–21, merged); these records reflect the final `main`.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-hand-rolled-orchestration.md) | Hand-rolled orchestration (no graph/workflow framework) | Accepted |
| [0002](0002-single-writer-memory-and-queue.md) | Single-writer durable memory; everything through the queue | Accepted |
| [0003](0003-master-pipeline-and-execution-modes.md) | Master Agent pipeline + six execution modes | Accepted |
| [0004](0004-governance-matrix-and-approval-gate.md) | Governance decision matrix + human approval gate | Accepted |
| [0005](0005-shell-safety-in-sandbox-not-skill.md) | Shell-execution safety enforced in code, not SKILL.md | Accepted |
| [0006](0006-gp-self-improvement-approved-not-autonomous.md) | GP self-improvement: variant/lineage/fitness, approved-not-autonomous | Accepted |
| [0007](0007-evolvable-target-kinds-and-config-eval.md) | Evolvable target kinds (prompt vs config) + side-effect-free config evaluation | Accepted |
| [0008](0008-live-swap-via-active-evolutions.md) | Live swap via `active_evolutions` in runtime read paths | Accepted |
| [0009](0009-classifier-determinism-and-routing-completeness.md) | Classifier determinism + routing completeness | Accepted |
| [0010](0010-semantic-recall-lazy-vec-guard.md) | Semantic recall behind a lazy sqlite-vec guard | Accepted |
| [0011](0011-vault-sync-polling-and-conflict-queue.md) | Vault sync via polling + conflict queue + unified audit | Accepted |
| [0012](0012-agent-envelope-directives-and-router-planning.md) | Model-call envelope, typed agent directives, router-owned workflow planning | Accepted |

ADRs 0001–0011 reflect the v0.1 build. **0012** records the post-v0.1
architecture-deepening refactors (candidates 05/06/08; PRs #26/#27/#28); it is the
one ADR that revises earlier documented wording (the CONTEXT.md "Model call" entry).

New decisions get the next number. Supersede rather than rewrite: set the old ADR's status to `Superseded by NNNN`.
