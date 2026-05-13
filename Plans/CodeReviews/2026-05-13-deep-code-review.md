# Ubongo Deep Code Review

Date: 2026-05-13
Reviewer mode: Deep static + targeted dynamic validation
Scope: Entire repository (`src/`, `config/`, `tests/`, working-tree staged artifacts)

## Findings (Ordered by Severity)

### 1) High - Skill prompt path traversal allows arbitrary local file reads

- Location:
  - `src/ubongo/skills.py:163`
  - `src/ubongo/skills.py:164`
  - `src/ubongo/skills.py:167`
- What is wrong:
  - Prompt paths from skill frontmatter are joined as `skill.dir / rel` and read directly.
  - No confinement check prevents `../` traversal or absolute path escapes.
- Why it matters:
  - A malicious or compromised skill definition can read files outside the skill directory (`.env`, keys, secrets).
  - Read content can be inserted into prompts and potentially exfiltrated to model providers.
- Evidence/repro:
  - Created a temporary skill with `prompts: { p: ../../outside.txt }`.
  - `skills.prompt("evil", "p")` returned content of external file (`TOPSECRET`).
- Recommendation:
  - Resolve path and enforce confinement under `skill.dir` (`resolve()` + relative check).
  - Reject absolute prompt paths and any traversal outside the skill root.
  - Add regression tests for traversal attempts.

### 2) High - Current user message is sent twice to the LLM each turn

- Location:
  - `src/ubongo/master.py:227`
  - `src/ubongo/runner.py:39`
  - `src/ubongo/runner.py:42`
- What is wrong:
  - `master.handle()` appends the user message to DB before runner execution.
  - `build_message_history()` recalls messages (which already include this user turn) and then appends `current_message` again.
- Why it matters:
  - Every turn duplicates the latest user message in model input.
  - This inflates token usage, perturbs generation behavior, and can bias routing/response quality.
- Evidence/repro:
  - Patched persona `complete()` to capture messages.
  - Observed payload: `[{'role':'user','content':'hello'}, {'role':'user','content':'hello'}]` for a single-turn input.
- Recommendation:
  - Decide one source of truth: either include current turn from DB recall or append it manually, not both.
  - Add a regression test asserting exactly one instance of current user message in runner input.

### 3) High - Queue delivery path breaks when a prior undelivered row exists

- Location:
  - `src/ubongo/delivery/queue.py:152`
  - `src/ubongo/delivery/queue.py:153`
  - `src/ubongo/delivery/queue.py:158`
- What is wrong:
  - `enqueue_for_delivery()` enqueues a new row and then dequeues globally.
  - If dequeued row is not the newly inserted `row_id`, flow is marked "inconsistent" and token is nulled.
- Why it matters:
  - Valid turns can skip delivery side-effects and remain undelivered indefinitely.
  - One stale queue row can poison subsequent deliveries.
- Evidence/repro:
  - Inserted an old undelivered row, then called `enqueue_for_delivery()`.
  - Got `DeliveryToken(row_id=None, after_send_payload=None)`; both old and new rows remained undelivered.
- Recommendation:
  - Fetch/send by inserted `row_id` (or atomically reserve the intended row) rather than asserting global dequeue equality.
  - Add tests for stale-row scenarios.

### 4) Medium - `after_send` failures are silently swallowed, then row is marked delivered anyway

- Location:
  - `src/ubongo/events.py:26`
  - `src/ubongo/events.py:30`
  - `src/ubongo/delivery/queue.py:174`
  - `src/ubongo/delivery/queue.py:178`
- What is wrong:
  - `events.dispatch()` catches and logs handler exceptions.
  - `flush_delivered()` proceeds to `mark_delivered()` regardless.
- Why it matters:
  - Durable side-effects (vault projection, future subscribers) can fail while queue says success.
  - Creates silent data-loss and audit inconsistency.
- Evidence/repro:
  - Registered an `after_send` handler that raises.
  - Row was still marked delivered (`delivered_at` set).
- Recommendation:
  - Support strict dispatch result propagation.
  - On critical handler failure, keep row pending or move to retry/dead-letter state.

### 5) Medium - `load_config(path=...)` cache semantics are incorrect

- Location:
  - `src/ubongo/config.py:58`
  - `src/ubongo/config.py:60`
  - `src/ubongo/config.py:64`
- What is wrong:
  - Global `_cache` returns immediately, ignoring different `path` arguments after first load.
- Why it matters:
  - Multi-config testing or alternate runtime config loads can silently use wrong settings.
  - Security and behavior toggles may not reflect intended file.
- Evidence/repro:
  - Loaded temp `a.yaml`, then `b.yaml`; second call still returned values from `a.yaml`.
- Recommendation:
  - Cache per resolved config path, or bypass cache when custom `path` differs.
  - Add tests covering multi-path loads.

### 6) Low - Repository hygiene risks in staged artifacts

- Location:
  - `.claude/worktrees/xenodochial-kalam-9751d2` (git index mode `160000`)
  - `.specstory/.project.json` and `.specstory/statistics.json` are tracked despite `.gitignore` entry
- What is wrong:
  - A worktree path is staged as a gitlink (submodule-like pointer).
  - Tracked generated telemetry files churn in commits.
- Why it matters:
  - Build/review reproducibility risk and commit noise.
- Recommendation:
  - Remove accidental gitlink from index unless intentional.
  - Untrack `.specstory` files if they are local-only state.

## Coverage and Confidence

- Full test suite currently passes (`216 passed`), but these issues are largely outside existing assertions.
- Findings 1-5 were validated with focused runtime reproductions, not static speculation.

## Suggested Test Additions

1. Queue:
   - stale undelivered row present -> new delivery still succeeds and is flushable.
   - `after_send` exception path -> row remains pending/retryable (or deterministic policy).
2. Runner/master:
   - exactly one current user message in outbound history payload.
3. Skills:
   - prompt path traversal attempts must raise.
4. Config:
   - two different config paths in same process must return distinct configs.

## Suggested Remediation Order

1. Fix queue stale-row handling and delivery semantics.
2. Fix skill prompt path confinement.
3. Fix message duplication in runner history construction.
4. Fix config cache keying by path.
5. Clean staged repository artifacts (gitlink/telemetry tracking).
