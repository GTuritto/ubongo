# 0010 — Semantic recall behind a lazy sqlite-vec guard

Status: Accepted
Date: 2026-06-04 (decision dates to Phase 20)

## Context

Recall was recency-only (the last-N messages), so relevant older turns fell out
of context. Adding embeddings introduces real risks: a network/endpoint
dependency on the turn hot path, a SQLite extension (`sqlite-vec`) that some
platforms block from loading, and a test suite that must not make embedding
calls. The spec also phrased semantic recall as an `after_recall` event handler.

## Decision

- **Best-effort, guarded seam (`memory/embeddings.py`).** `vec_available()` tries
  to load `sqlite-vec` and create the `vec0` tables **once per connection**,
  caching the result; it returns False when embeddings are disabled or the
  extension can't load. `embed()` returns None on any failure. Vec tables are
  created **lazily on first use**, not in `bootstrap()`, so an embeddings-off run
  (and the entire test suite, via `UBONGO_DISABLE_EMBEDDINGS`) never touches the
  extension.
- **Computed in `recall()`, not an `after_recall` handler.** The `after_recall`
  event fires *after* `RecallContext` is built, so a handler can't inject
  results. `recall(conversation_id, query)` does the KNN search and returns
  `semantic_messages`; the runner already has the query to pass. The event stays
  for observers. A documented, minor deviation from the spec wording.
- **Idempotent writes.** Messages index on write at the single `store.append_message`
  seam; an `embedding_meta` text-hash sidecar skips the embed call for unchanged
  text. An embedding never blocks a message commit.

## Consequences

- Disabled / extension-blocked / endpoint-down all degrade cleanly to
  recency-only with no errors — graceful degradation is a first-class property.
- The blast radius is tiny: nothing in the existing turn path changes behavior
  when embeddings are off.
- The embedding provider is OpenRouter's `text-embedding-3-small` (1536-dim) with
  the existing key — verified, no new credential.

References: `Plans/phase-20-embeddings-graph.md`; `src/ubongo/memory/{embeddings,store,graph}.py`, `src/ubongo/runner.py`.
