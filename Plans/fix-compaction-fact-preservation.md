# Fix — Compaction Fact Preservation + Smoke Playbook Drift

Date: 2026-05-12
Branch: `fix-compaction-fact-preservation` (off `main`)
Triggered by: end-to-end smoke run after Phase 6 merge.

## What broke

### Issue 1 (real bug) — Cumulative summary drops named facts when later turns are repetitive

Smoke scenario 4.4 told Ubongo `"My birthday is March 15. Remember that."`, then ran 15 turns of `"just say ok"` to force compaction, then asked `"What's my birthday?"`. Expected reply: names March 15. Expected summary: contains March 15.

Actual:
- Reply: `"Ok"`.
- Latest summary: *"User prefers brief responses and has requested the assistant respond with only 'Ok' to their messages."* — no March 15, no birthday.

The plumbing is verified by `tests/test_memory_compaction.py::test_cumulative_summary_folds_prior_into_new` (passes). What fails is the **summarizer LLM**: when later turns saturate the conversation pattern, Haiku 4.5 rewrites the summary around the pattern and drops the substantive earlier facts. This cascades into scenario 4.5: cross-session inheritance ships an empty-of-facts summary forward.

The compaction prompt in [src/ubongo/memory/compaction.py:33-34](../src/ubongo/memory/compaction.py#L33) already says `"Always preserve concrete facts, names, dates, preferences, and decisions stated by the user."` That instruction isn't strong enough to override the model's tendency to over-summarize when input is repetitive.

### Issue 2 (docs drift) — Smoke playbook 1.7 help-line text

After Phase 6 the unknown-command help line is:

> `Unknown command: /foo. Try /architect, /operator, /casual, /auto, /skill <name>, /skills, /summary, /reload, /exit.`

[tests/manual/smoke_test.md:35](../tests/manual/smoke_test.md#L35) still quotes the pre-Phase-6 short list. Functional pass; docs lag.

### Issue 3 (docs drift) — Smoke playbook 4.3 covers_to arithmetic

[tests/manual/smoke_test.md:70](../tests/manual/smoke_test.md#L70) claims the latest summary's `covers_to_message_id = max - 10`. This is only true when compaction fired on the most recent recall. Compaction fires on `after_recall` (user-turn boundary), not on every message. With `trigger_at_turns: 15`, fold spacing is every ~6 messages of new content above the floor. So after 16 turns (32 msgs), the last fold ran at msg 27, leaving `covers_to=17`, not 22. The mechanism is correct; the playbook's formula is too simple.

## What this plan does NOT cover

- 3.3 hysteresis: not a bug; classifier was confidently casual (0.9). Skipping.
- Switching the compaction model from Haiku to Sonnet: more cost per fold, and the prompt-fix should be enough. Out of scope; revisit if Issue 1 reappears after the prompt fix.
- A Phase 8-style structured-fact-extraction layer: that's the right long-term answer (extract named entities into a `facts` table — already on the v0.1 spec for Phase 9 Memory Agent). Issue 1 is about making the v0.1 compaction prompt good enough until Phase 9 lands the durable facts surface.

## Branch + commit strategy

Branch off `main` at `37955a9`. Three commits:

- **Fix 1a** — compaction prompt + regression test.
- **Fix 1b** — smoke playbook 1.7 + 4.3 corrections.
- (final, if necessary) STATUS sync (just a date bump, may fold into 1a).

## Implementation

### 1a — Strengthen the compaction prompt + add a regression test

**Change:** rewrite `_DEFAULT_SYSTEM_PROMPT` and the user-message scaffolding in [src/ubongo/memory/compaction.py](../src/ubongo/memory/compaction.py) so:

1. The system prompt frames the existing summary as a **non-negotiable carry-forward**: every named entity, date, number, identifier, preference, and "remember this" instruction from the prior summary must be preserved verbatim or paraphrased without semantic loss. The model may add to the summary but never delete factual content from it.
2. The user message is restructured to make the carry-forward visually prominent: prior summary first under a clearer "FACTS TO PRESERVE" frame, new turns labeled as "additions to fold in."
3. The prompt explicitly tells the model: if the new turns are repetitive or pattern-shaped (e.g., all the same response), summarize them in **one sentence** at the end and keep the existing facts intact.

**New system prompt (proposed):**

```text
You maintain a running summary of an ongoing conversation. The summary is the durable memory the assistant relies on for facts older than the recall window.

Hard rules:
1. The existing summary (when one is provided) is a carry-forward. Every named entity, date, number, identifier, preference, "remember this" instruction, and concrete decision in it must appear in your output. You may rephrase, but you may not drop any of these.
2. When the new turns are repetitive or pattern-shaped (e.g., the same short reply repeated), summarize the pattern in ONE sentence at the end. Do not let the pattern overwrite earlier facts.
3. Always preserve concrete facts, names, dates, numbers, identifiers, preferences, and decisions stated by the user in the new turns too.
4. Drop banter and pleasantries.

Format: under 200 words, third person, plain prose, single paragraph or two short paragraphs at most.
```

**New user-message scaffolding (when there is a prior summary):**

```text
## Existing summary (FACTS TO PRESERVE — every named entity, date, number, and 'remember this' from here must appear in your output)

{prior_summary}

## New turns to fold in

{transcript}

Write the updated summary now. Carry every fact above forward; integrate any new facts from the transcript; if the transcript is repetitive, describe the pattern in one sentence.
```

(When there is no prior summary, keep the existing single-section format — there's nothing to carry forward.)

**Regression test:** add `test_default_strategy_preserves_named_facts_under_repetitive_tail` to [tests/test_memory_compaction.py](../tests/test_memory_compaction.py). Mock the LLM to return a deterministic input-echo (so the test verifies the prompt, not the model). Specifically:

- Build a `prior_summary = "User's birthday is March 15. They are working on a project called Ubongo."`
- Build `messages = [Message(...) x 15]` all with role-alternating `"just say ok"` / `"Ok"`.
- Call `default_strategy(prior_summary, messages)`.
- Assert the `complete()` call's `messages[0]["content"]` contains both `"March 15"` and `"Ubongo"`.
- Assert the `complete()` call's `system_prompt` contains the hard-rule about not dropping facts.

This locks the prompt shape, not the model output. The behavioral test that catches the live failure remains the manual smoke 4.4 — which I'll re-run after the fix to confirm.

**Files modified:**
- `src/ubongo/memory/compaction.py` (prompt + user-message scaffolding).
- `tests/test_memory_compaction.py` (one new test).

### 1b — Smoke playbook corrections

**Change `tests/manual/smoke_test.md`:**

1. Row 1.7 — update expected help line to the full Phase 6 version: `Unknown command: /foo. Try /architect, /operator, /casual, /auto, /skill <name>, /skills, /summary, /reload, /exit.`
2. Row 4.3 — replace `at least one row with covers_to_message_id = (max - 10)` with a more accurate criterion: `at least one row exists in summaries with strategy='default' and a covers_to_message_id ≤ max - recall_turns; covers_to advances on each subsequent fold.` Removes the misleading exact-equality claim. Also clarifies that compaction fires on the user-message boundary of `after_recall`, so fold cadence depends on `trigger_at_turns` and not on every message.

**Files modified:** `tests/manual/smoke_test.md`.

## Verification

After implementing 1a and 1b:

1. `pytest` — all existing tests pass, the new regression test passes.
2. Re-run smoke 4.4: with a fresh DB, drive the birthday + 15-filler-turn scenario, then ask `"What's my birthday?"`. Expected reply names March 15. `sqlite3` query on `summaries` returns content containing `"March 15"`.
3. Re-run smoke 4.5: with the same DB plus a 31-minute time jump, ask `"Do you know my birthday?"` — reply still names March 15 (proves cross-session inheritance carries the fact).
4. Re-run smoke 1.7 unknown-command — output matches the updated playbook row.
5. Re-run smoke 4.3 — confirm summaries exist with the relaxed criterion.

If smoke 4.4 still fails with the new prompt, the fallback is to upgrade the compaction model from Haiku to Sonnet in [config/settings.yaml](../config/settings.yaml). That's a separate decision (cost) and not part of this fix — I'd come back to you before flipping it.

## Out of scope

- Structured fact extraction (`facts` table) — Phase 9 territory (Memory Agent).
- Sonnet for compaction — cost decision; deferred unless prompt fix is insufficient.
- 3.3 hysteresis behavior — not a bug.
- Refactoring `_DEFAULT_SYSTEM_PROMPT` location into config — Phase 16+ when GP-targets need to evolve prompts.

## Open questions to confirm before I start

1. **Strengthen the compaction prompt + add regression test (recommended)?** This is my proposed fix. The alternative is to just bump the model — but I'd rather not pay Sonnet rates per fold if a better prompt does the job. OK?
2. **Regression test mocks the LLM** so it locks the *prompt shape* — that the prior summary's facts are placed in the user message and the system prompt contains the hard-rule. The live-LLM verification stays in the manual smoke. OK?
3. **Playbook 4.3 — replace the `max - 10` claim with the relaxed criterion (recommended)?** Alternative is to deduce the exact covers_to from `(turns_since_first_compaction, trigger_at_turns, recall_turns)` arithmetic — accurate but reads like a number puzzle. I lean toward the relaxed criterion.
4. **Branch name `fix-compaction-fact-preservation`?** Phase-style is `phase-N-<name>`; for a fix branch that's not a phase I'd use `fix-<name>`. OK?

If you don't push back, I'll proceed with the defaults.

## Definition of done

- Three commits on `fix-compaction-fact-preservation` (1a, 1b, optionally STATUS).
- New compaction regression test passes; full pytest stays green.
- Manual smoke 4.4 passes: reply names "March 15"; latest summary contains "March 15".
- Manual smoke 4.5 passes as a cascade of 4.4.
- Manual smoke 1.7 row matches new behavior.
- Manual smoke 4.3 row has the relaxed criterion and an explanation of fold cadence.
- Branch handed to you for merge. Don't merge.
