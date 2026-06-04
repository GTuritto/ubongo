# Phase 20 — Embeddings + Graph: Implementation Plan

Date: 2026-06-04
Branch: `phase-20-embeddings-graph` (off `main`, after PR #12 merges — see Sequencing)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 20 (lines ~1291–1320).
Tier: 6 — Wiki Memory + Polish (first phase).

## Context

Recall today is recency-only: `store.recall(conversation_id)` returns the latest
summary plus the last-N messages. Old-but-relevant turns fall out of the window
and are lost to the model. Phase 20 adds **semantic recall** (embed the current
query, retrieve the most similar prior messages even if old) via `sqlite-vec`,
and a **vault-link graph** (parse `[[wikilinks]]` in daily notes, populate
`vault_links`, expose traversal). It is the first phase of Tier 6.

Verified during planning (the two unknowns that shape the design):

- **The embedding provider works.** `litellm.embedding(model=
  "openrouter/openai/text-embedding-3-small", input=[...])` returns 1536-dim
  vectors using the existing `OPENROUTER_API_KEY` — OpenRouter serves this
  embeddings model. No new key, no local model, no OpenAI fallback needed.
- **`sqlite-vec` 0.1.9 is installed** (already in `pyproject.toml`); it loads as a
  SQLite extension and provides `vec0` virtual tables.

Already in place: the `memory.embeddings` config block (`enabled: true`,
`model`, `recall_top_k: 5`), the `vault_links` table (empty), and the Memory
Agent as the single durable writer.

## Goal

Semantic recall augments recency in the turn context and in a new `/recall`
command; the Memory Agent writes message embeddings idempotently; daily-note
`[[wikilinks]]` populate `vault_links` and are queryable via a graph API. When
embeddings are disabled or the extension/endpoint is unavailable, the system
degrades cleanly to recency-only with no errors.

## Design decisions

### Embeddings live behind a guarded, lazy seam (`memory/embeddings.py`)

- **`embed(texts: list[str]) -> list[list[float]] | None`** wraps
  `litellm.embedding` with the configured model. Best-effort: on any failure it
  logs and returns `None` (callers fall back to recency). One batched call per
  write/recall.
- **`sqlite-vec` loading is lazy and guarded.** A `vec_available()` helper tries
  `conn.enable_load_extension(True)` + `sqlite_vec.load(conn)` **once**, caches
  the result, and returns False if the platform blocks extension loading or
  `embeddings.enabled` is false. The core `bootstrap()` is untouched — vec setup
  happens on first embedding use, so the ~50 test temp-DBs and any
  embeddings-off run never load the extension or create vec tables. This keeps
  the change isolated and the suite fast.
- **Vec tables (created lazily when first used):**
  `vec_messages USING vec0(embedding float[1536])` keyed by `message_id` rowid,
  and `vec_vault USING vec0(embedding float[1536])` keyed by a vault-doc rowid.
  Dimension is read from a first embedding (1536) and asserted on reuse.

### Idempotent embedding writes (`20b`)

A sidecar **`embedding_meta(message_id INTEGER PK, text_hash TEXT, embedded_at
TIMESTAMP)`** table (plain, portable). The Memory Agent, after committing a
message, embeds it only if `message_id` is absent or its stored `text_hash`
differs from the current text's hash. Re-running on an existing DB makes **no**
embedding calls for unchanged messages (scenario 2). Writes are best-effort:
an embedding failure never blocks the message commit (the single-writer contract
and the turn must not depend on the embedding endpoint).

### Semantic recall is computed in `recall()`, not an `after_recall` handler

The spec phrases 20c as "semantic recall handler on `after_recall`", but the
`after_recall` event fires *after* `recall()` has already built and is about to
return `RecallContext` — a handler can't inject results into it. Cleaner:
`recall(conversation_id, query: str | None = None)` gains the query, and when a
query + embeddings are available it runs a `vec_messages` KNN search for the
top-`recall_top_k` most similar messages **outside the recency window** (and in
this conversation or, optionally, cross-conversation), returning them on an
extended `RecallContext.semantic_messages`. `runner.build_message_history`
already has `current_message` and passes it as the query, folding the semantic
hits into the context (clearly delimited from recency history). The
`after_recall` event stays exactly as-is for compaction/observers. This is a
documented, minor deviation from the literal wording, in service of the same
outcome.

### Vault-link graph (`20d`, `20e`)

- **Extraction:** when the Memory Agent projects a turn to a daily note, parse
  `[[wikilink]]` targets from the rendered text and upsert `vault_links(source=
  today's note path, target=linked note, link_type='wikilink', created_at)`.
  Idempotent via the table's composite PK.
- **`memory/graph.py`:** `neighbors(path) -> list[str]` (outbound + inbound
  links), `backlinks(path)`, and a bounded `traverse(path, depth)` for the
  link graph. Pure SQL over `vault_links`.

### `/recall` REPL command (`20f`)

`/recall [query]` prints the recency window and, when embeddings are on, the
semantic hits (with similarity), plus the vault-graph neighbors of today's note.
With no query it recalls against the latest user message. A direct tool (no
`master.handle`), like `/trace`.

### Graceful degradation (`scenario 5`) is a first-class requirement

Every embedding path is best-effort. `embeddings.enabled: false`, a platform
that blocks extension loading, or an embedding-endpoint failure all collapse to
**recency-only with no errors**: `embed()` returns None, `vec_available()`
returns False, `recall()` skips the semantic block, and the Memory Agent skips
embedding writes. A unit test pins this.

## Files touched

New:

- `src/ubongo/memory/embeddings.py` — `embed()`, `vec_available()`, vec-table
  setup, `index_message()`, `search_messages(query_vec, k, exclude_ids)`.
- `src/ubongo/memory/graph.py` — `neighbors`, `backlinks`, `traverse`.

Modified:

- `src/ubongo/memory/store.py` — `recall(conversation_id, query=None)` +
  `RecallContext.semantic_messages`; `embedding_meta` + `vault_links` accessors;
  schema gains `embedding_meta` (plain table, `CREATE TABLE IF NOT EXISTS`).
- `src/ubongo/memory/schema.sql` — `embedding_meta` table (vec0 tables are
  created lazily in code, not here).
- `src/ubongo/agents/memory.py` — embed-on-commit (idempotent) + `[[wikilink]]`
  extraction into `vault_links`.
- `src/ubongo/memory/vault.py` — a `parse_wikilinks(text)` helper.
- `src/ubongo/runner.py` — pass `current_message` as the recall query; fold
  `semantic_messages` into context.
- `src/ubongo/repl.py` — `/recall` command + help string.
- `config/settings.yaml` — no change (the `embeddings` block already exists).

No destructive migration (only an additive `embedding_meta` table; vec tables
are created on demand).

## Tests

Unit (`tests/`), `embed()` mocked (deterministic fake vectors), no real network:

- `test_embeddings.py` — `vec_available` guard (true when loadable, false when
  disabled/blocked); `index_message` + `search_messages` KNN ordering with fake
  vectors; idempotency (re-index unchanged text makes no embed call; changed
  text re-embeds); dimension mismatch guarded.
- `test_semantic_recall.py` — `recall(conv, query)` returns semantic hits
  outside the recency window; excludes the recency ids; respects `recall_top_k`;
  with embeddings disabled returns recency-only (scenario 5, no error).
- `test_graph.py` — `parse_wikilinks`; `vault_links` populated from a note;
  `neighbors` / `backlinks` / bounded `traverse`.
- `test_memory_embeddings_writes.py` — Memory Agent embeds on commit
  idempotently; an embed failure does not block the message commit.
- `test_repl_recall.py` — `/recall` renders recency + semantic + graph
  neighbors; degrades cleanly with embeddings off; `recall` in help.

Spec scenario coverage:

| # | Scenario | Covered by |
| --- | --- | --- |
| 1 | Semantic recall surfaces old turns | `test_semantic_recall` + live smoke |
| 2 | Embedding idempotency | `test_embeddings` + `test_memory_embeddings_writes` |
| 3 | Vault graph from `[[wikilink]]` | `test_graph` |
| 4 | `/recall` lists recency + semantic | `test_repl_recall` |
| 5 | Without embeddings → recency-only | `test_semantic_recall` + `test_repl_recall` |

## Smoke additions

Append a Phase 20 section to `tests/manual/smoke_test.md`: seed a "caching"
discussion, run enough unrelated turns to push it out of the recency window,
then ask "remember our caching discussion" and confirm the old turns surface
via `/recall` (semantic); add `[[note-a]]` to a daily note and confirm a
`vault_links` row + `neighbors`; flip `embeddings.enabled: false` and confirm
recency-only with no errors. The live semantic step needs `OPENROUTER_API_KEY`.

## Risks / honest flags

- **Live embedding cost + latency.** One embedding call per new message (write)
  and one per recall query. Cheap (small model) but non-zero; batched where
  possible, best-effort, and skippable via the flag.
- **Extension-loading portability.** `enable_load_extension` is available in the
  project's Python sqlite build (verified: `sqlite_vec` imports), but some
  builds disable it. The `vec_available()` guard turns that into graceful
  recency-only rather than a crash.
- **Recall semantics.** Cross-conversation semantic recall could surface
  unrelated context; v0.1 scopes the KNN search to the current conversation by
  default, with a flag to widen later. Documented.

## Sequencing

1. **PR #12 (classifier/routing fix) should merge to `main` first** so Phase 20
   branches off a `main` that includes it (no rebase later). If you'd rather not,
   I can branch Phase 20 off `fix-classifier-routing` instead — your call.
2. First commit on the branch also flips STATUS.md Phase 19 → Complete (merged
   via PR #11, `d3841a5`) — the deferred doc fix.
3. Implement in order: 20a embeddings seam + vec guard → 20b idempotent writes →
   20c semantic recall in `recall()` → 20d/e vault graph + `graph.py` → 20f
   `/recall`. Tests alongside each.
4. Full pytest (currently 676 with the fix; Phase 20 adds ~30) + the Phase 20
   smoke section + a bounded live run (seed old context, confirm semantic
   recall).
5. Mark ready; user reviews, user merges. Phase 21 (the last v0.1 phase) only
   after this merges.

## Out of scope

- Bidirectional vault sync / file watcher / conflict gating → Phase 21.
- Re-embedding on model change / vector migration tooling → not v0.1.

## Acceptance

- All five spec scenarios pass; smoke passes; full pytest green.
- Semantic recall surfaces relevant old turns; idempotent embedding writes;
  `vault_links` populated from `[[wikilinks]]` with a graph API; `/recall` works;
  clean recency-only degradation when embeddings are off.
- No destructive migration; vec tables created lazily behind a guard so
  embeddings-off / extension-blocked environments are unaffected.
- LOC stays under the ~15k soft target.
