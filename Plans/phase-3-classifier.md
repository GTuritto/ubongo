# Phase 3 — Tone Classifier + Auto Routing: Implementation Plan

Date: 2026-05-10
Branch: `phase-3-classifier` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 751–779; routing schema lines 555–574.

## Goal

In `/auto` mode the REPL classifies each user turn and picks the persona automatically. Manual slash overrides still beat auto. Hysteresis prevents flapping on a single low-confidence outlier. Classifier failures fall back to the default persona without crashing.

## Why this plan exists

Phase 3 introduces the second LLM call per turn (small classifier model) and the first pieces of routing logic. Both will be reused heavily: Phase 8's Master Agent inherits classification + routing as private helpers; Phase 17's GP fitness scores classifier prompts as evolution targets. The schema we lock in for `Classification` (the dataclass) is the contract both later phases consume — getting the field set right matters more than the visible feature for Phase 3.

## Branch + commit strategy

Branch already cut. Five commits, one per sub-phase, plus a final STATUS + smoke commit. Six total.

## Sub-phases

### 3a — Classifier function

**Purpose:** A `classify(message: str) -> Classification` function that calls the small classifier model with a strict JSON-output instruction, parses defensively, and returns a `Classification` dataclass. On failure, returns a degenerate `Classification(confidence=0.0)` and logs the cause.

**Tasks:**

1. Create `src/ubongo/classifier.py`:
   - `@dataclass(frozen=True) Classification`: `intent: str`, `tone: str`, `task_type: str`, `suggested_skill: str | None`, `risk: str`, `confidence: float`. Vocab definitions:
     - `intent`: `technical | casual | work | research | coding | other`
     - `tone`: `neutral | frustrated | excited | tired | curious`
     - `task_type`: `command | high_stakes_decision | question | chat | none`
     - `suggested_skill`: skill name or `None` (always `None` until Phase 6)
     - `risk`: `low | medium | high | destructive`
     - `confidence`: 0.0–1.0
   - `_FALLBACK = Classification("other", "neutral", "none", None, "low", 0.0)` — what we return when the classifier fails.
   - `classify(message: str) -> Classification`:
     1. `events.dispatch("before_classify", {"message_length": len(message)})` (passthrough; payload omits message body — privacy and log bloat).
     2. Build a strict prompt: "Reply with ONLY a JSON object with exactly these keys: intent, tone, task_type, suggested_skill, risk, confidence. Allowed values: ..." plus the message.
     3. Call `llm.complete(system_prompt, [{"role":"user","content":message}], settings.classifier_model, max_tokens=128)` with a low max_tokens cap.
     4. Strip code fences if present (LLMs love wrapping JSON in `\`\`\`json`); attempt `json.loads`.
     5. Validate every field is present and within vocab; coerce confidence to `[0.0, 1.0]`. Any deviation → `_FALLBACK`.
     6. `events.dispatch("after_classify", {"classification": result_dict})`.
     7. Return the `Classification`.
2. `_classifier_model_from_config()` reads `models.classifier` via `load_config()` (already cached).

**Files touched:** `src/ubongo/classifier.py` (new).

**Decisions flagged:**
- **Vocab is whitelisted, not free-form.** A model that returns `intent: "philosophy"` falls back, not assigns a stray label. Keeps downstream routing predictable.
- **The classifier system prompt does NOT load `UBONGO.md`.** UBONGO.md is for personas; the classifier needs to be a stateless tagger, not channel Giuseppe's voice. Hierarchical loading is for response-producing prompts.
- **`max_tokens=128`.** A JSON object with six fields easily fits. Caps cost on noisy returns.

### 3b — Routing logic

**Purpose:** Given a `Classification`, return the persona name to use. Uses `config/routing.yaml`'s rule set; first rule whose `match` block is fully satisfied wins; otherwise `default_workflow`'s persona.

**Tasks:**

1. Create `config/routing.yaml` from spec verbatim (the seven rules + `default_workflow: casual_reply`).
2. Create `src/ubongo/router.py`:
   - `_WORKFLOW_TO_PERSONA: dict[str, str]` — Phase-3 shortcut. Hardcoded `{"technical_deep": "architect", "quick_action": "operator", "casual_reply": "casual", "supportive_reply": "casual", "research_brief": "architect", "coding_session": "architect", "debate_then_synthesize": "architect", "speculative_brief": "operator"}`. Phase 8 replaces this with a `workflows.yaml` reader.
   - `_load_routing()` reads `config/routing.yaml` once, caches.
   - `route(classification: Classification) -> str` (returns persona name):
     - Load rules.
     - For each rule, check the `match` block: all key-value pairs in `match` must equal the corresponding fields in `classification`. First match wins.
     - Map workflow → persona via `_WORKFLOW_TO_PERSONA`. Unknown workflow name → log + fall back to default workflow.
     - If no rule matched, use `default_workflow` (`casual_reply` → `casual`).
3. `reload()` clears the routing cache for the future `/reload` command.

**Files touched:** `config/routing.yaml` (new), `src/ubongo/router.py` (new).

**Decisions flagged:**
- **Hardcoded workflow→persona map in Phase 3.** Spec's `workflows.yaml` carries persona + agents + mode + risk per workflow; Phase 3 needs only the persona field. Reading the full workflows.yaml now means writing a parser for fields nothing consumes yet. The 8-entry map gets deleted in Phase 8 when workflows.yaml lands. Documented in code as a phase-scoped shortcut.
- **First-match-wins** is deliberate. Order in routing.yaml encodes priority. The user can re-order rules to change behavior without changing code.

### 3c — Hysteresis

**Purpose:** When in `/auto`, only switch the active persona if `(new_persona != current_persona) AND (confidence >= 0.7)`. A single low-confidence classification doesn't yank the conversation around.

**Tasks:**

1. In `src/ubongo/router.py`, add `apply_hysteresis(current_persona: str, suggested: str, confidence: float, threshold: float = 0.7) -> str`. Returns the persona to use on this turn.
   - If `suggested == current_persona`: return `current_persona` (no change).
   - If `confidence < threshold`: return `current_persona` (sticky).
   - Else: return `suggested`.
2. `threshold` defaults to 0.7 per spec; reading from `settings.governance.confidence_threshold_for_auto` is a Phase-14 enhancement (when governance reads the same value). For Phase 3, hardcode 0.7 — a single literal that Phase 14 lifts.

**Files touched:** `src/ubongo/router.py` (continuing 3b).

**Decision flagged:** The threshold is a literal in Phase 3, not a config read. Settings.yaml has `governance.confidence_threshold_for_auto: 0.7`; Phase 14 starts using it. For now, the literal is fine — making it configurable in Phase 3 means three places need to agree on the source-of-truth, and there's no consumer to verify the linkage.

### 3d — Events wiring

**Purpose:** `before_classify` and `after_classify` events fire as passthroughs (no handlers in Phase 3; Phase 8 subscribes). Already covered by `events.dispatch` calls in 3a; this sub-phase is mostly about test coverage.

**Tasks:**

1. Verify `before_classify` and `after_classify` are dispatched with the documented payloads (already in 3a).
2. Add a test in `tests/test_classifier.py` that registers a handler and confirms it fires for both events.

**Files touched:** `tests/test_classifier.py` (continuing).

**Decision flagged:** No production handler registered. The seam exists; Phase 8 wires the Master Agent's per-turn trace through these.

### 3e — Per-turn classification log + REPL/oneshot wiring

**Purpose:** Tie classifier + router into the REPL turn loop behind a new `auto_mode` flag. Manual personas (`/architect`/`/operator`/`/casual`) disable auto. `/auto` enables it. The log gets a `classify` event per auto-turn with the full classification + chosen persona.

**Tasks:**

1. Modify `src/ubongo/repl.py`:
   - Add `auto_mode: bool` to the loop state. Initial value: `False`.
   - When user types `/auto`: set `auto_mode = True`, set `persona = DEFAULT_PERSONA` (no classification yet — Phase-3 notice goes away), confirm with a one-line message: `Auto routing enabled.` (replaces the Phase-1 `Auto routing not yet active...` notice).
   - When user types `/architect|/operator|/casual`: set `auto_mode = False`, set persona accordingly. (Manual override beats auto.)
   - In `handle_text`: if `auto_mode`, call `classifier.classify(message)` → `router.route(...)` → `router.apply_hysteresis(current, suggested, confidence)`; use the resulting persona for this turn AND update `current_persona` so the next turn's hysteresis baseline reflects the switch. Log a `classify` event with `{intent, tone, task_type, risk, confidence, suggested, used}`.
2. `oneshot.py` does not get auto mode in Phase 3. The `--persona` flag (or default) is the only path. Spec scenarios are REPL-only.
3. The persona returned from `apply_hysteresis` is what `handle_text` uses for that turn. The REPL tracks the new "current" so hysteresis is correct on the next turn.

**Files touched:** `src/ubongo/repl.py` (modified), `src/ubongo/oneshot.py` (no changes; documented).

**Decisions flagged:**
- **`/auto` slash dispatch needs to return more than `(persona, keep_going, msg)`.** The current `handle_slash` signature can't communicate "set auto mode". I'll either widen the tuple to `(persona, keep_going, msg, auto_mode_change)` OR just have `/auto` return the new persona and let the loop check `cmd == "auto"` separately. I prefer widening the return: it keeps the slash dispatch as the single source of mode truth. Concretely: return `(new_persona, keep_going, msg, new_auto_mode)` where `new_auto_mode` is `True` for `/auto`, `False` for the named personas, and `None` (no change) for `/exit` and unknown commands. Updates `tests/test_repl.py` accordingly.
- **`current_persona` updates inside `handle_text`.** This is a small departure from Phase 2 where `handle_text` was stateless. The cleanest implementation: `handle_text` returns `(text, ok, new_persona)` so the REPL loop can update its local `persona` variable. Manual mode passes through unchanged; auto mode may rebind `persona`.

## Final file tree after Phase 3

```text
src/ubongo/
  classifier.py    (new)
  router.py        (new)
  repl.py          (modified — auto_mode flag, classifier/router wiring)
  oneshot.py       (unchanged in code; updated docstring noting --persona only)
  ...
config/
  routing.yaml     (new)
  ...
tests/
  test_classifier.py  (new)
  test_router.py      (new)
  test_repl.py        (modified — slash dispatch return-type widened)
Plans/
  phase-3-classifier.md  (new — this file)
STATUS.md              (modified — Phase 3 row + LOC)
tests/manual/smoke_test.md (modified — Phase 3 section populated)
```

Untouched: `agents/personas.py`, `llm.py`, `events.py`, `context.py`, `config.py`, `logging.py`, `__main__.py`. The Master Agent (Phase 8) consumes classifier+router; not building it yet.

## Testing plan (from spec, made concrete)

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Auto-route to architect | REPL: `/auto`, `help me design a circuit breaker` | Switches to architect; substantive technical response. `classify` log shows `intent=technical|coding`, `used=architect`, `confidence>=0.7`. |
| 2 | Auto-route to casual | REPL: `/auto`, `ugh long day` | Switches to casual; warm short reply. `classify` log shows `tone=tired|frustrated` or `intent=casual`. |
| 3 | Hysteresis | REPL: `/auto`, five technical messages, then `lol` | After `lol`, persona stays architect (because either `lol` classifies as casual but with confidence < 0.7, or because `apply_hysteresis` rejects the flip). `classify` log on the `lol` turn shows the suggested persona but the `used` field is the kept one. |
| 4 | Manual override beats auto | REPL: `/auto`, technical question (auto picks architect), `/casual`, `something simple` | After `/casual`, `auto_mode=False`; the next turn uses casual without classifier call. No `classify` event for that turn. |
| 5 | Classifier failure | Patch the classifier model env to a bogus value (or feed a malformed response via test); ask any question in `/auto` | `classifier.classify` returns `_FALLBACK`; router falls back to `default_workflow` → casual; log emits `classify_failed` with cause. Conversation continues. |

Plus pytest:

| # | Pytest | Expected |
| --- | --- | --- |
| pytest | `tests/test_classifier.py` (new), `tests/test_router.py` (new), `tests/test_repl.py` (slash dispatch tuple widened), existing personas/events tests | All pass. ~25 tests total. |

For `test_classifier.py` I'll mock `llm.complete` rather than hit the network — defensive parsing is the heart of the function and a mock is unambiguously the right test boundary. For `test_router.py` no LLM at all (pure logic over routing.yaml).

## Smoke playbook updates

Append a Phase 3 section to `tests/manual/smoke_test.md` with the five scenarios from the table. Phase 1's test 1.3 (`/auto` notice) needs its expected text updated: in Phase 1+ the message was `Auto routing not yet active (Phase 3); using default persona: architect.` In Phase 3 it becomes `Auto routing enabled.` plus the next turn auto-classifies. Update Phase 1's row 1.3 accordingly.

## Out of scope for Phase 3 (do NOT build)

- SQLite memory, sessions, persistent history (Phase 4).
- Vault projection (Phase 5).
- Skills (Phase 6); `suggested_skill` always `None` for now.
- Outbound queue (Phase 7).
- Master Agent and workers (Phase 8).
- `workflows.yaml` (Phase 8 introduces; Phase 3 uses a hardcoded workflow→persona shortcut).
- Real risk/governance behavior (Phase 14); the `risk` field in `Classification` is captured but not used for any decision in Phase 3.
- Streaming, cost-aware routing, model fallback, classifier-prompt evolution (Phase 17 evolves the classifier prompt as a GP target).

## Open questions to confirm before I start

1. **`/auto` initial persona.** When user types `/auto` without a prior message, do I (a) leave the active persona unchanged and only re-classify on the NEXT text turn, or (b) reset to `architect` immediately? I lean (a) — simpler and avoids a phantom switch. Override?
2. **`Classification.intent` vocab.** I'm proposing `technical | casual | work | research | coding | other`. The routing.yaml in the spec uses `technical`, `casual`, `work`, `research`, `coding`. Adding `other` covers the off-vocabulary case so we don't have to silently coerce. OK?
3. **Hysteresis threshold = 0.7 hardcoded.** Spec says 0.7. settings.yaml also has `governance.confidence_threshold_for_auto: 0.7`. I'll hardcode the literal in `router.py` for Phase 3; Phase 14 lifts it to the settings read. Override?
4. **Classifier failure scenario in pytest vs manual.** Test 5 says "force JSON parse error". I'll do this two ways: (a) a pytest test that mocks `llm.complete` to return junk and asserts `_FALLBACK` is returned; (b) a manual scenario in the smoke playbook that uses an injection trick (e.g., a prompt that confuses small models). Smoke test relies on the unit test; manual scenario is a sanity check, not the gate. OK?
5. **`/auto` confirmation message wording.** I'm going with `Auto routing enabled.` Anything you'd rather see, like `Auto: classifier picks per turn.`?

If you don't push back on any of these, I'll go with the defaults above.

## Definition of done for Phase 3

- Six commits on `phase-3-classifier`.
- Manual smoke scenarios 1–5 pass interactively.
- New pytest for classifier and router; existing tests still pass.
- `tests/manual/smoke_test.md` Phase 3 section populated; Phase 1's row 1.3 updated for the `/auto` message change.
- `STATUS.md` Phase 3 row → Complete (2026-05-10); LOC count updated.
- Branch handed to you for merge. Don't merge.
