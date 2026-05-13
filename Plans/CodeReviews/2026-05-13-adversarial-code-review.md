# Ubongo Adversarial Code Review

Date: 2026-05-13
Scope: Entire repository (`src/`, `config/`, `tests/`, staged working tree artifacts)
Mode: Adversarial security/reliability review (threat model -> discovery -> validation -> attack-path)

## Executive Summary

The codebase is generally clean and well-tested (`216` tests passing), but there are **two high-severity integrity/security flaws** and several medium risks that can cause silent data loss, broken delivery semantics, or accidental secret/file exposure.

## Threat Model (Repository-Scoped)

- Primary assets:
  - conversation history and summaries in SQLite
  - vault daily notes (long-term memory projection)
  - local secrets (`.env`, API keys)
  - delivery/audit trail (`notification_queue`, governance/workflow rows)
- Trust boundaries:
  - user input -> LLM prompts
  - skill metadata/files -> prompt assembly
  - queue delivery -> `after_send` side-effects
  - local git state -> what gets published/merged
- High-impact failure modes:
  - dropped or reordered outputs
  - silent loss of durable memory/audit logs
  - local file read/exfil via prompt-loading path controls

## Findings

### 1) HIGH - Queue delivery can fail hard when any prior undelivered row exists

- Affected code:
  - `src/ubongo/delivery/queue.py:152`
  - `src/ubongo/delivery/queue.py:153`
  - `src/ubongo/delivery/queue.py:158`
- Root cause:
  - `enqueue_for_delivery()` inserts row `row_id`, then calls `dequeue_deliverable()` globally and requires `row.id == row_id`.
  - If any older undelivered row exists, the check fails as "inconsistent".
- Impact:
  - current response still prints, but `before_send`/`after_send` path for that row is skipped and row is never marked delivered.
  - repeated turns can accumulate undelivered rows and degrade delivery semantics.
- Validation:
  - Reproduced locally with a stale row present; new enqueue returned `DeliveryToken(row_id=None, after_send_payload=None)` and both rows remained undelivered.

### 2) HIGH - Skill prompt path traversal allows arbitrary local file read (and potential secret exfil)

- Affected code:
  - `src/ubongo/skills.py:163`
  - `src/ubongo/skills.py:164`
  - `src/ubongo/skills.py:167`
- Root cause:
  - `path = skill.dir / rel` accepts untrusted relative `prompts` paths from SKILL frontmatter with no confinement to `skill.dir`.
- Impact:
  - malicious or compromised skill metadata can read arbitrary files outside skill directory (`../../.env`, SSH keys, etc.).
  - read content is then usable in prompts and can be sent to external LLM providers.
- Validation:
  - Reproduced with temporary skill defining `prompts.p: ../../outside.txt`; `skills.prompt('evil','p')` returned external file content.

### 3) MEDIUM - Silent durable-memory loss when `after_send` handlers fail

- Affected code:
  - `src/ubongo/events.py:26`
  - `src/ubongo/events.py:30`
  - `src/ubongo/delivery/queue.py:174`
  - `src/ubongo/delivery/queue.py:178`
- Root cause:
  - `events.dispatch()` swallows handler exceptions.
  - `flush_delivered()` always proceeds to `mark_delivered()` even when `after_send` failed.
- Impact:
  - queue row marked delivered although vault projection or other side-effects failed.
  - no retry path; data-loss is silent except a warning log.
- Validation:
  - Reproduced by registering an `after_send` handler that raises; row still ended as delivered.

### 4) MEDIUM - Config cache path confusion returns wrong file after first load

- Affected code:
  - `src/ubongo/config.py:58`
  - `src/ubongo/config.py:60`
  - `src/ubongo/config.py:64`
- Root cause:
  - global `_cache` is returned before considering `path` argument.
- Impact:
  - tools/tests/runtime paths that request a different config file can silently get stale config.
  - can cause security controls to be read from the wrong settings file.
- Validation:
  - Reproduced with two temp configs (`a.yaml`, `b.yaml`); second load still returned `a` values.

### 5) MEDIUM - Staged gitlink under `.claude/worktrees` is a repository supply-chain/hygiene risk

- Affected tree state:
  - `.claude/worktrees/xenodochial-kalam-9751d2` staged as mode `160000` (gitlink)
- Root cause:
  - nested worktree was staged as submodule-like pointer.
- Impact:
  - clones/CI may fail or resolve to unexpected commit state.
  - accidental publication of local worktree references can break reproducibility and review integrity.
- Validation:
  - `git ls-files -s` shows mode `160000` entry.

### 6) LOW - Ignored telemetry artifacts are tracked and churn in diffs

- Affected files:
  - `.specstory/.project.json`
  - `.specstory/statistics.json`
  - `.gitignore` includes `.specstory/`
- Root cause:
  - files were tracked before ignore rules.
- Impact:
  - noisy diffs and commit contamination.
  - indirect risk of publishing local operational metadata.

## Attack Path Notes

- Most severe practical chain: compromised skill definition -> path traversal read of local secret file -> secret injected into model prompt -> external exfiltration via model provider.
- Most severe reliability chain: stale queue row or handler failure -> queue token null or premature delivered mark -> missing vault/audit state despite successful visible response.

## Recommended Remediations (Priority Order)

1. Queue correctness
   - In `enqueue_for_delivery`, fetch by inserted `row_id` directly (or atomically reserve/mark that row), not by global dequeue equality checks.
   - Add regression test: pre-existing undelivered row must not break delivery of current row.

2. Skills path confinement
   - Resolve and enforce `path.resolve().is_relative_to(skill.dir.resolve())` (or equivalent prefix check for Python compatibility).
   - Reject absolute paths and `..` escapes in skill prompt paths.
   - Add regression tests for traversal attempts.

3. After-send durability semantics
   - If `after_send` fails, do not mark delivered; keep row pending or move to retry/dead-letter state.
   - Return handler error status from `dispatch()` or provide strict dispatch mode.

4. Config cache behavior
   - Cache per-path or bypass cache when `path` argument differs from default.
   - Add tests that `load_config(path=a)` and `load_config(path=b)` can coexist correctly.

5. Repository hygiene
   - Remove accidental gitlink and optionally ignore `.claude/worktrees/`.
   - Untrack `.specstory/*` if these should remain local-only artifacts.

## Verification Performed

- Full test run: `uv run pytest -q` -> `216 passed`
- Targeted runtime reproductions executed for Findings 1, 2, 3, and 4.

