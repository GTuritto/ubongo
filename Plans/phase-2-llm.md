# Phase 2 — LLM Integration: Implementation Plan

Date: 2026-05-10
Branch: `phase-2-llm` (off `main`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) lines 721–749.

## Goal

REPL and one-shot mode call OpenRouter through LiteLLM using hierarchical prompts. Each persona uses a different model and token budget declared in its frontmatter. The echo is gone; real responses replace it. Personas feel different. Errors fail gracefully.

## Why this plan exists

Phase 2 is the first phase that actually talks to a model. Five things lock in here that everything downstream inherits: the `CompletionResult` shape (used by Evaluator in Phase 10 and the GP fitness function in Phase 17), the retry policy, the event-bus surface (`before_llm` / `after_llm`), the persona-frontmatter schema (model + max_tokens), and the error-handling contract. Getting the seams right matters more than the visible feature.

## Branch + commit strategy

Branch already cut. Five commits, one per sub-phase, plus a final STATUS + smoke-playbook commit. Six commits total. Final 2e commit message says "Phase 2 complete."

## Sub-phases

### 2a — Persona registry

**Purpose:** Load each persona file once at startup; expose `get(name) -> Persona` returning `(body, model, max_tokens)`.

**Tasks:**

1. Create `src/ubongo/agents/__init__.py` (empty marker).
2. Create `src/ubongo/agents/personas.py`:
   - `@dataclass Persona`: `name: str`, `body: str`, `model: str`, `max_tokens: int`.
   - `_registry: dict[str, Persona]` populated lazily on first `get()`.
   - `get(name: str) -> Persona`: reads `config/personas/<name>.md`, parses YAML frontmatter, looks up the model string. Frontmatter's `default_model` is a key into `settings.yaml`'s `models` map (`default`, `casual`, `coding`, etc.) — never a raw model string. Forces all model identifiers to live in one place.
   - `reload() -> None`: clears the registry. Future `/reload` will call this.
3. Update the three persona files (`architect.md`, `operator.md`, `casual.md`) to add `default_model` and `max_tokens` to frontmatter:
   - architect: `default_model: default`, `max_tokens: 1024`
   - operator: `default_model: default`, `max_tokens: 256`
   - casual: `default_model: casual`, `max_tokens: 512`
4. The body returned by `get()` is the post-frontmatter content (reuses `_strip_frontmatter` logic from `context.py`; do not duplicate — import or extract a shared helper).

**Files touched:** `src/ubongo/agents/__init__.py` (new), `src/ubongo/agents/personas.py` (new), `config/personas/architect.md`, `config/personas/operator.md`, `config/personas/casual.md`.

**Decision flagged:** Frontmatter's `default_model` indirects through `settings.yaml`. So if you later swap `models.default` from claude-sonnet-4.5 to claude-opus-4-7, every persona that uses `default` updates automatically. The alternative (raw model strings in frontmatter) means N edits for one model change. The downside: if a persona genuinely wants a model not listed in `settings.yaml`, you have to add it there first. I think that's correct — model identifiers belong in config, not in persona files.

### 2b — LiteLLM wrapper

**Purpose:** A single function `complete(system_prompt, messages, model, max_tokens) -> CompletionResult` that wraps `litellm.completion`, handles a single retry on transient failure, and emits the LLM event hooks.

**Tasks:**

1. Create `src/ubongo/llm.py`:
   - `@dataclass CompletionResult`: `text: str`, `model: str`, `tokens_in: int`, `tokens_out: int`, `latency_ms: int`, `attempts: int`. Used downstream for fitness scoring.
   - `class LLMError(Exception)`: terminal error after retry exhausted. Carries `cause` for logging.
   - `complete(system_prompt: str, messages: list[dict], model: str, max_tokens: int) -> CompletionResult`:
     1. `events.dispatch("before_llm", {"model": model, "max_tokens": max_tokens, "messages_count": len(messages)})`
     2. Build the LiteLLM call: `litellm.completion(model=model, messages=[{"role":"system","content":system_prompt}, *messages], max_tokens=max_tokens)`.
     3. Try up to twice with a short backoff (0.5s) between attempts. Catch any exception in the first attempt; raise `LLMError(cause=e)` if the second also fails.
     4. Parse the response: text from `response.choices[0].message.content`; tokens from `response.usage.prompt_tokens` / `response.usage.completion_tokens`; latency from a wall-clock timer.
     5. `events.dispatch("after_llm", {"model": ..., "tokens_in": ..., "tokens_out": ..., "latency_ms": ..., "attempts": ...})`
     6. Return the `CompletionResult`.
2. The OpenRouter API key resolution is implicit — `litellm.completion` reads `OPENROUTER_API_KEY` from the environment, and our `config.load_config()` already triggers `load_dotenv()`.

**Files touched:** `src/ubongo/llm.py` (new).

**Decisions flagged:**
- **Retry policy: one retry, fixed 0.5s backoff, retry on any exception.** Spec says "Single retry on transient errors." Distinguishing transient from terminal is fragile (LiteLLM error taxonomy varies by upstream); a single all-purpose retry is simple and matches the spec literally. Phase 11 (Repair Agent) introduces smarter retry strategies.
- **No streaming.** Spec doesn't ask. Adds complexity for no Phase-2 benefit.
- **`CompletionResult.attempts` field.** Forward-looking; Phase 17 fitness uses it.

### 2c — Wire into REPL and one-shot

**Purpose:** Replace the echo in `repl.py` and `oneshot.py` with a real LLM call. Each turn: get persona → build system prompt → call LLM → print response.

**Tasks:**

1. Modify `src/ubongo/repl.py`:
   - In `_handle_text` (now renamed `handle_text` since tests touch it):
     - `persona = personas.get(persona_name)` (the resolved Persona record).
     - `system_prompt = build_system_prompt(persona_name)` (existing context loader; reuse).
     - `messages = [{"role": "user", "content": text}]` for Phase 2 (no in-session history; Phase 4 lifts this).
     - `result = llm.complete(system_prompt, messages, persona.model, persona.max_tokens)`.
     - `print(result.text)` (response goes to stdout, not the `[persona] ...` bracket form — that was echo formatting that real responses replace).
     - Log a `repl_turn` event with `persona`, `length`, `model`, `tokens_in`, `tokens_out`, `latency_ms`.
2. Modify `src/ubongo/oneshot.py` similarly.
3. The `/architect|/operator|/casual|/auto|/exit` slash dispatch is unchanged; only text-turn handling changes.

**Files touched:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py`.

**Decision flagged:** No in-session history in Phase 2. Each user turn is a single-message exchange. This means the REPL can't follow up on prior turns within a session. It will feel limited, but it matches the spec's progressive build (Phase 4 adds memory). Adding history now means inventing a session abstraction that Phase 4 then replaces — wasted work. Flagging this so you don't think it's a regression.

### 2d — Event scaffolding

**Purpose:** Stand up the named-event dispatcher that every later phase plugs into. Phase 2 only registers `before_llm` and `after_llm` as passthrough handlers (logging only).

**Tasks:**

1. Create `src/ubongo/events.py`:
   - `_handlers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)`.
   - `register(event: str, handler: Callable[[dict], None]) -> None`.
   - `dispatch(event: str, payload: dict) -> None`: calls handlers in registration order; exceptions in handlers are caught and logged (one bad handler doesn't break the chain).
   - `clear() -> None`: for tests.
2. In Phase 2, no handlers are registered by default. The `dispatch()` calls in `llm.py` are no-ops until something subscribes. The seam exists; Phase 5 (`after_send` for vault projection) and Phase 8 (`agent_started` etc.) start using it.

**Files touched:** `src/ubongo/events.py` (new).

**Decision flagged:** Synchronous dispatch only. Async event handling lands in Phase 12 with `asyncio` workflows. For Phase 2, sync is enough and avoids dragging asyncio into the LLM path.

### 2e — Error path

**Purpose:** When LLM calls fail terminally (bad API key, rate limit, network), the user sees a polite stdout message and the cause is logged structured. The REPL keeps running; one-shot exits 1.

**Tasks:**

1. In `repl.py` `handle_text`: wrap `llm.complete(...)` in `try/except LLMError`. On error: print `Sorry, I couldn't reach the model. Check the logs.` to stdout; log `llm_error` with cause to stderr; loop continues.
2. In `oneshot.py` `run`: same try/except. On error: print the same polite message to stdout; log `llm_error`; return 1.
3. No tracebacks to stdout. Tracebacks go through the standard logger to stderr (the `JsonFormatter` already serializes `exc_info`).

**Files touched:** `src/ubongo/repl.py`, `src/ubongo/oneshot.py`.

**Decision flagged:** A single generic error message is fine for Phase 2. Phase 13 (Repair Agent) gets to differentiate transient vs terminal and surface different messages. Don't predict that taxonomy now.

## Testing plan

The five spec scenarios (real LLM calls) plus existing pytest tests. Real model calls cost money and require the network — I'll run them once interactively rather than scripting them.

| # | Scenario | Steps | Expected |
| --- | --- | --- | --- |
| 1 | Architect mode | REPL: `design a circuit breaker for an API gateway` | Substantive response with tradeoffs. Persona voice (architect) is recognizable. |
| 2 | Casual mode | REPL: `/casual`, `ugh today sucked` | Short, warm reply. Different feel from architect. |
| 3 | Operator mode | REPL: `/operator`, `summarize my last 3 commits` | Terse reply (LLM may caveat about not having git access — acceptable). |
| 4 | UBONGO.md effect | Edit `config/UBONGO.md` to add a quirky preference (e.g., "Always begin replies with 'Right.'"); restart REPL; ask any question | Response respects the new preference. |
| 5 | LLM error | Set `OPENROUTER_API_KEY=invalid`; ask any question | stdout: polite error. stderr: structured `llm_error` log line. No traceback to stdout. |

Plus pytest:

| # | Pytest | Expected |
| --- | --- | --- |
| pytest | `tests/test_repl.py` (existing 9 slash tests) + `tests/test_personas.py` (new) + `tests/test_events.py` (new) | All pass. |

I'll add unit tests for:
- `personas.get()` returns the right model and max_tokens for each persona
- `personas.get()` raises on unknown persona
- `events.register` + `events.dispatch` calls handlers in order
- `events.dispatch` swallows handler exceptions and continues

LLM wrapper itself (`llm.complete`) is hard to unit-test without either mocking LiteLLM (brittle) or hitting the network (expensive). I'll skip pytest for `llm.py` and rely on manual scenarios 1–5 instead. If you'd rather I mock it, say so.

## Smoke playbook updates

Append a Phase 2 section to `tests/manual/smoke_test.md` with the five scenarios above. Phase 1's bracket-echo scenarios (1.1, 1.2, 1.3) need their "Expected" columns updated — they will no longer produce `[persona] message`; they'll produce real LLM responses. Specifically:

- Test 1.1 expected becomes: "Banner shown; substantive architect-voiced response to `hello`."
- Test 1.2 expected becomes: "After `/casual`: warm casual response."
- Test 1.3 expected becomes: "After `/auto`: Phase-3 notice + architect-voiced response."
- Tests 1.4 (`/exit`), 1.5/1.6 (one-shot), 1.7 (unknown slash), 1.9 (EOF), 1.10 (pytest) are unchanged.
- Test 1.8 (one-shot bad persona) is unchanged.

The bracket-echo format itself is gone, but persona switching, slash dispatch, and exit behavior stay identical.

## Out of scope for Phase 2 (do NOT build)

- Tone classification or auto routing (Phase 3).
- SQLite memory, sessions, persistent history (Phase 4).
- Vault projection, daily notes (Phase 5).
- Skills, progressive disclosure (Phase 6).
- Outbound queue, `/queue` command (Phase 7).
- Multi-message conversation history within a REPL session (Phase 4 introduces it via memory).
- Streaming responses.
- `/reload` command (no spec phase claims it explicitly; defer until a phase needs it).
- Smart retry strategies (Phase 13).
- The Master Agent — Phase 8. Phase 2 talks to LiteLLM directly from `handle_text`. Phase 8 inserts a `MasterAgent.handle()` between the REPL and the LLM.

## Open questions to confirm before I start

1. **In-session history.** I'm planning **no** in-session history for Phase 2 — each turn sends only the current message to the LLM, so follow-ups won't work within a REPL session. Phase 4 fixes this. Alternative: add an in-process list now (rough Phase 4 preview, ~10 LOC). Strict spec adherence says skip; UX says add. I lean skip. Override?
2. **Persona-frontmatter `default_model` indirection.** Frontmatter would say `default_model: default` (key into `settings.yaml`'s `models` map) rather than `default_model: openrouter/anthropic/claude-sonnet-4.5`. Centralizes model identifiers. Object?
3. **Default `max_tokens` per persona.** architect 1024, operator 256, casual 512. Tunable later. OK as a starting point?
4. **Pytest for `llm.complete`.** Skip (rely on manual scenarios) vs mock LiteLLM (brittle but unit-testable). I lean skip; the five manual scenarios cover the real paths. Override?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 2

- Six commits on `phase-2-llm`.
- All five spec scenarios pass interactively.
- New pytest tests for `personas` and `events` pass; existing `test_repl.py` still passes.
- `tests/manual/smoke_test.md` Phase 2 section populated; Phase 1 expected columns updated for the bracket-echo removal.
- `STATUS.md` Phase 2 row → Complete (2026-05-10); LOC count updated.
- Branch handed to user for merge. Don't merge.
