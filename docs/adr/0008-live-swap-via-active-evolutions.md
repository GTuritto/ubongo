# 0008 — Live swap via `active_evolutions` in runtime read paths

Status: Accepted
Date: backfilled 2026-06-04 (decision dates to Phase 19)

## Context

A promotion must actually change behavior, or the self-improvement loop is theatre. The subtlety found during Phase 19: `targets.resolve_base` already read `active_evolutions`, but only the *generator* called it — the live persona agent reads the persona *file* via `context.build_system_prompt`, and the router reads `routing.yaml` / `workflows.yaml`. Approving a promotion changed what the loop mutated *from*, not what the system *did*.

## Decision

Make the **runtime read paths** consult `active_evolutions` directly, so an approved promotion swaps behavior immediately:

- Persona: `context.build_system_prompt` uses the promoted `variant_text` as the persona body (the file frontmatter still supplies model/max_tokens).
- Routing: `router.route_workflow` uses the promoted routing config (via the same effective-config precedence: eval-override > promotion > file).
- Tool chain: `router.workflow_agents` uses the promoted agent list.

All reads are guarded by `store.is_connected()` so pure prompt assembly in a process with no DB never bootstraps one. Approve/reject/rollback bust the relevant caches (`context`, `personas`, `router`) so the swap takes effect within the running REPL. Rollback clears the `active_evolutions` row and reverts to file/default.

## Consequences

- Promotion is real and immediately reversible; one `active_evolutions` row per target is the single source of truth for "what's live."
- The read paths now have a DB dependency on the hot path (guarded, best-effort) — verified not to disturb core turn behavior.

References: `Plans/phase-19-promotions.md`; `src/ubongo/{context,router}.py`, `src/ubongo/evolution/promotion.py`, `src/ubongo/memory/store.py` (`active_evolution`, `is_connected`).
