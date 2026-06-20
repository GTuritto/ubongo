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
| [0013](0013-self-authored-skills-quarantine-and-approval.md) | Self-authored skills: quarantine + human approval boundary | Accepted |
| [0014](0014-local-only-observability-profiler.md) | Local-only observability: in-process profiler over the run tables, no telemetry export | Accepted |
| [0015](0015-mcp-server-additive-channel.md) | MCP server as an additive channel: no bypass, approval stays human, reads read-only | Accepted |
| [0016](0016-connector-agent-external-tools-one-seam.md) | External tools behind one Connector seam; first-class tool layer deferred | Accepted |

ADRs 0001–0011 reflect the v0.1 build. **0012** records the post-v0.1
architecture-deepening refactors (candidates 05/06/08; PRs #26/#27/#28); it is the
one ADR that revises earlier documented wording (the CONTEXT.md "Model call" entry).
**0013** records the post-v0.1 self-extension experiment — the `authoring/` package,
where Ubongo drafts brand-new skills behind a quarantine + human-approval boundary.
**0014** records the v0.1.3 observability posture: the local profiler reads the run
tables in-process and nothing telemetric ever leaves the machine.
**0015** records the v0.1.4 MCP server channel: machine-facing, same one-seam turn
pipeline, gates never approvable over MCP, LAN no-auth posture shared with the web UI.
**0016** records the v0.1.5 MCP client: external tools behind the one Connector seam,
opt-in routing, irreversible + per-server risk — the first-class tool layer stays
unjustified.

The **v0.5 trust protocol** adds: **0017** the deployment envelope (rootless Podman +
nftables egress, Linux-only); **0018** the typed, persisted, resumable approval seam
(approve in any channel); **0019** the grant registry (standing consent, checked after
the safety rules); **0020** Telegram, the first cloud-relayed channel; **0021** standing
jobs — proactive output through the existing seams, with the quiet-hours + raise-TTL
default-deny posture for "no human at run time."

New decisions get the next number. Supersede rather than rewrite: set the old ADR's status to `Superseded by NNNN`.
