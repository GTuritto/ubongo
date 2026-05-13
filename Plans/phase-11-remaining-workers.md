# Phase 11 — Coding + Execution + Repair Agents: Implementation Plan

Date: 2026-05-13
Branch: `phase-11-remaining-workers` (off `main` at `beca817`)
Spec source: [UBONGO_BUILD.md](../UBONGO_BUILD.md) §Phase 11 (lines 996–1025), Worker Agents table (105–129), Constrained-bash + Execution paragraph (475–477), Repair Agent description (125–126), Skills + Progressive Disclosure (Phase 6 references).

## Goal

Three remaining workers ship; the `coding_session` workflow goes live; one debug-only REPL command exposes the sandbox directly:

1. **Coding Agent** (`agents/coding.py`) — strong-coding-model LLM call. `composer=True`. Used by the `coding_session` workflow.
2. **Constrained-bash skill + sandbox module** — the skill is `config/skills/constrained-bash/` (frontmatter declares `risk: medium`, `reversibility: irreversible`); the actual enforcement lives in a new `src/ubongo/sandbox.py` so the safety contract is in code, not in a markdown body the LLM could rewrite. v0.1 enforcement: explicit command allowlist + `subprocess.run` with `shell=False`, scrubbed env (PATH restricted), `cwd` confined to the repo root, 10s default timeout.
3. **Execution Agent** (`agents/execution.py`) — `composer=False`. Reads its target command from `input.metadata.exec_command` (set by Master for `execution_session` workflow OR by the `/exec` REPL command), invokes `sandbox.run_constrained`, returns stdout/stderr/exit-code in the result text. Refuses cleanly on disallowed commands.
4. **Repair Agent** (`agents/repair.py`) — `composer=False`. Phase 11 ships a registration + a minimum-viable single-retry path: when the Workflow Runner sees `agent_failed`, it asks the Repair Agent (synchronously, in the runner) for a single retry with a model fallback before continuing. Phase 13 expands to multi-step recovery + rollback.
5. **`/exec <cmd>` REPL command** — debug-only; routes directly through `sandbox.run_constrained`, does NOT create a `workflow_runs` row, does not enqueue, does not touch the vault. Output to stdout.
6. **`coding_session` workflow lit up** — `workflows.yaml` updated to `agents: ["coding", "architect"]` (Coding produces, Architect wraps + explains; both composers, Architect runs last so its text is the final response). `evaluate: true` stays. Phase 12 may revisit.

**Non-goals for Phase 11 (locked):**
- Real sandboxing (Phase 15 brings filesystem allowlists, seccomp / chroot, network restrictions). Phase 11 is "good enough to demo + not catastrophic by accident."
- Multi-step Repair recovery (rollback partial workflow state, replace stuck agent with peer, etc.) — Phase 13.
- Approval prompts for high-risk commands — Phase 15 (`governance/approval.py`).
- A first-class `Execution` workflow auto-routed from the classifier; in Phase 11 the only Execution paths are `/exec` (debug) and a future `execution_session` workflow (which Phase 11 declares in `workflows.yaml` but doesn't auto-route via `routing.yaml`).
- Writing a Python tool registry / tool-use API — keeping new tools as CLI scripts invoked through skills per the CLAUDE.md "New tools default to CLI scripts" rule.

## Why this plan exists

Three patterns Phase 11 locks in that Phases 12–15 inherit:

1. **The safety contract lives in `sandbox.py`, not in `SKILL.md`.** A `SKILL.md` body is a markdown prompt the LLM-side composer reads. Anything that affects what runs on the user's machine must be enforced in code that the LLM can't rewrite. Phase 15 will harden this; Phase 11 establishes the seam (one module owns "what shells can run; under what env; with what timeout"). Execution Agent calls `sandbox.run_constrained(...)`; nothing else does.
2. **Repair is a runner-level concern.** The runner sees the failure first (`agent_failed` event already exists in `runner.py:163`). Phase 11 wires the Repair Agent into the runner's failure path with a `retried` boolean on `agent_runs` so the trace stays honest. Phase 13 extends — different model, peer-agent replacement, rollback — without moving the call site.
3. **Composer ordering in multi-composer workflows is "last wins."** `coding_session` has `("coding", "architect")` — both composer-true. The runner's existing `last_composer_result` rule (Phase 10) returns the architect's text because it runs last. Coding's text goes into `prior_findings` so Architect can quote it verbatim. This is the same pattern Phase 12 will lean on for collaborative / debate modes; Phase 11 is the first place it's used in production.

## Branch + commit strategy

Branch: `phase-11-remaining-workers` off `main` at `beca817` (HEAD; Phase 10 + smoke patches landed).

Per the new project rule (`feedback_phase_branch_open_draft_pr.md`): push the branch and open a **draft PR** immediately after this plan commit, base `main`. PR title: `Phase 11 — Coding + Execution + Repair Agents`. PR stays draft until 11g lands.

Seven commits matching the spec's sub-phase letters + one for STATUS:

- **11a** — `agents/coding.py`: CodingAgent. Tests.
- **11b** — `src/ubongo/sandbox.py` + `config/skills/constrained-bash/SKILL.md` + `config/skills/constrained-bash/prompts/run.md`. Allowlist, restricted PATH, timeout, repo-root cwd. Tests.
- **11c** — `agents/execution.py`. Reads command from `input.metadata.exec_command`, calls sandbox, returns AgentResult with formatted output. Tests.
- **11d** — `agents/repair.py` + runner wiring. RepairAgent registered; runner consults it on `agent_failed`. `agent_runs.retried` boolean column (schema migration). Tests.
- **11e** — `repl.py`: `/exec <cmd>` slash command + parser + renderer. Tests.
- **11f** — `config/workflows.yaml`: `coding_session: agents=["coding", "architect"]`; add `execution_session: agents=["execution", "architect"]` (declared but not auto-routed). Tests.
- **11g** — `STATUS.md` + `tests/manual/smoke_test.md` Phase 11 section + scenario 1.7 help-line tweak (`/exec`).

## Sub-phases

### 11a — Coding Agent (`src/ubongo/agents/coding.py`)

**Purpose:** A strong-coding-model LLM call with a coding-focused system prompt. Used by `coding_session` workflow. Distinct from PersonaAgent (different system prompt, different default model) but otherwise structurally similar.

**Tasks:**

1. Create `src/ubongo/agents/coding.py`:

   ```python
   class CodingAgent:
       name = "coding"
       role = "code generation, refactoring, review"
       composer = True

       def __init__(self) -> None:
           cfg = load_config()
           models = cfg.get("models", {})
           self.default_model = models.get("coding") or models.get("default", "")
           self.max_tokens = int(
               cfg.get("agents", {}).get("coding", {}).get("max_tokens", 2048)
           )

       def run(self, input: AgentInput, context) -> AgentResult: ...
   ```

2. System prompt: borrows architect voice (depth + tradeoffs) but adds a coding-specific stanza:

   ```
   {build_system_prompt("architect", agent_role="coding")}

   You are the Coding Agent. Produce working code. When the user asks for code:
   - Write the function/module they asked for; do not write a plan instead.
   - Include type hints, docstrings, and one usage example.
   - Name what you assumed when the spec was ambiguous.
   - If the request is too broad to write in one pass, ask for the one concrete
     thing you need to know — don't write a half-implementation.
   ```

3. `run()` body mirrors PersonaAgent.run but with the Coding system prompt + the coding model. Wraps `prior_findings` as `## Prior agent findings #N` (same shape as PersonaAgent). LLMError → `AgentResult(ok=False, error="coding_llm_error")`.

4. **Coding tokens budget.** Higher than personas (2048 default) because code blocks consume tokens fast. Configurable via `agents.coding.max_tokens` in `settings.yaml`.

5. **`settings.yaml`** already lists `models.coding`. Add `agents.coding.max_tokens: 2048`.

6. Tests in `tests/test_agents_coding.py` (~5):
   - Happy path: mocked LLM returns a code block; `ok=True`, `composer=True`, text contains the block.
   - LLMError: `ok=False, error="coding_llm_error"`, no exception escapes.
   - Default model + max_tokens resolve from settings.
   - System prompt includes the coding stanza (asserted via captured `complete` kwargs).
   - `prior_findings` are threaded into the system prompt under `## Prior agent findings`.

**Files added:** `src/ubongo/agents/coding.py`, `tests/test_agents_coding.py`.
**Files modified:** `config/settings.yaml` (+`agents.coding.max_tokens`).

### 11b — Constrained-bash skill + sandbox module

**Purpose:** Establish the safety contract for any shell execution. The skill file is metadata + a prompt template (for the Execution Agent to read); the enforcement is in `sandbox.py`.

**Tasks:**

1. Create `src/ubongo/sandbox.py`:

   ```python
   """Constrained shell execution. Phase 11 v0.1 enforcement: an explicit
   command allowlist + restricted PATH + repo-root cwd + 10s default timeout.

   Phase 15 will harden this with filesystem allowlists, env scrubbing
   beyond PATH, and (when feasible on macOS+Linux) seccomp / chroot.
   """

   ALLOWED_COMMANDS: frozenset[str] = frozenset({
       "ls", "pwd", "echo", "cat", "head", "tail", "wc", "grep", "find",
       "git", "python", "python3", "pip", "uv", "pytest", "sqlite3",
       "true", "false",
   })

   _SAFE_PATH = "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"
   _DEFAULT_TIMEOUT_SECONDS = 10

   class SandboxRefused(Exception):
       """Raised when a command is rejected before execution."""

   @dataclass(frozen=True)
   class SandboxResult:
       stdout: str
       stderr: str
       exit_code: int
       latency_ms: int
       argv: tuple[str, ...]

   def run_constrained(cmd: str, *, timeout: int | None = None) -> SandboxResult: ...
   ```

2. `run_constrained` body:
   - Parse `cmd` with `shlex.split(cmd, posix=True)`. If splitting yields zero tokens → `SandboxRefused("empty command")`.
   - First token is the program; reject if not in `ALLOWED_COMMANDS`.
   - Reject any token containing `..`, `/etc`, `/var`, `/usr/local/var`, or starting with `~` (defense in depth — argument-side traversal). Phase 15 replaces this with a proper allowlist of paths.
   - Reject any token containing shell metacharacters (`;`, `|`, `&`, `` ` ``, `$(`, `>`, `<`). Phase 11 deliberately disallows pipes/redirects in the wrapper — the user can rephrase as multiple sequential commands at the Execution-Agent level if needed.
   - `subprocess.run(argv, shell=False, env={"PATH": _SAFE_PATH, "HOME": str(repo_root)}, cwd=repo_root, timeout=timeout_value, capture_output=True, text=True)`.
   - Catch `TimeoutExpired` → return `SandboxResult(stdout=partial_stdout, stderr="(timed out)", exit_code=-1, …)`.
   - Catch `FileNotFoundError` (program in allowlist but not installed) → `SandboxResult(stderr=f"(not installed: {prog})", exit_code=-1, …)`.
   - All other exceptions: log + return `SandboxResult(stderr=str(exc)[:200], exit_code=-1, …)`. The Execution Agent translates these into `AgentResult.ok=False` via its own logic.

3. Create `config/skills/constrained-bash/SKILL.md`:

   ```yaml
   ---
   name: constrained-bash
   description: Run a single shell command from an allowlist (ls, cat, grep, git, python, pytest, sqlite3, etc.) inside the repo root. v0.1; sandboxing is minimal (Phase 15 will harden it). risk: medium, reversibility: irreversible.
   risk: medium
   reversibility: irreversible
   default_persona: operator
   prompts:
     run: prompts/run.md
   ---

   The constrained-bash skill lets the Execution Agent run a single shell command
   under a static allowlist. v0.1 enforcement: shlex parse, allowlist check,
   restricted PATH, repo-root cwd, 10s default timeout, no pipes or redirects.
   ```

4. Create `config/skills/constrained-bash/prompts/run.md`:

   ```
   You have one tool: a constrained shell. Use it sparingly. The allowed
   commands are: ls, pwd, echo, cat, head, tail, wc, grep, find, git, python,
   python3, pip, uv, pytest, sqlite3, true, false. No pipes, no redirects, no
   shell metacharacters; one program per call. Working dir is the repo root.
   Timeout: 10 seconds.

   If the user's request is broader than one command, pick the single most
   informative command and explain what the next would be after seeing this
   output.

   You will receive the command's stdout, stderr, and exit code verbatim.
   ```

5. Tests in `tests/test_sandbox.py` (~10):
   - `run_constrained("echo hello")` returns stdout `"hello\n"`, exit_code 0.
   - `run_constrained("pwd")` returns the repo root.
   - `run_constrained("cat /etc/passwd")` → `SandboxRefused` (path argument rejected).
   - `run_constrained("ls; rm -rf /")` → `SandboxRefused` (metacharacter rejected).
   - `run_constrained("ls | grep x")` → `SandboxRefused` (pipe rejected).
   - `run_constrained("rm -f /tmp/foo")` → `SandboxRefused` (program not in allowlist).
   - `run_constrained("")` → `SandboxRefused` (empty).
   - `run_constrained("ls ../../etc")` → `SandboxRefused` (path traversal).
   - Empty PATH outside `_SAFE_PATH` means `python` runs but `vim` doesn't: just assert `_SAFE_PATH` is the env's PATH inside a captured subprocess by spawning a child that prints `$PATH` (a `python3 -c "import os; print(os.environ['PATH'])"` style check).
   - `run_constrained("python3 -c 'import time; time.sleep(2)'", timeout=1)` → exit_code -1, stderr contains "timed out".

6. Tests in `tests/test_skills_discovery.py` (existing, add 1): the new `constrained-bash` skill loads with `risk=medium, reversibility=irreversible, default_persona=operator`.

**Files added:** `src/ubongo/sandbox.py`, `config/skills/constrained-bash/SKILL.md`, `config/skills/constrained-bash/prompts/run.md`, `tests/test_sandbox.py`.
**Files modified:** `tests/test_skills_discovery.py` (+1 assertion).

### 11c — Execution Agent (`src/ubongo/agents/execution.py`)

**Purpose:** Bridge from a target command (handed in via `input.metadata.exec_command` or pulled from `input.message`) to `sandbox.run_constrained`. Format the result as a structured findings string that downstream agents (persona, evaluator) can read.

**Tasks:**

1. Create `src/ubongo/agents/execution.py`:

   ```python
   class ExecutionAgent:
       name = "execution"
       role = "runs shell scripts via constrained-bash skill"
       composer = False
       default_model = ""  # no LLM call in v0.1

       def __init__(self, *, timeout: int | None = None) -> None:
           self.timeout = timeout

       def run(self, input: AgentInput, context) -> AgentResult: ...
   ```

2. `run()` flow:
   - Locate the command:
     - Prefer `input.metadata.get("exec_command")` (set by Master / `/exec`).
     - Fall back to a "pull first fenced code block from `input.message`" heuristic. v0.1 keeps this dumb: if the message contains exactly one ` ```sh ... ``` ` or ` ```bash ... ``` ` block, use that. Otherwise: `ok=False, error="execution_no_command"`.
   - Invoke `sandbox.run_constrained(cmd, timeout=self.timeout)`. Catch `SandboxRefused` → `AgentResult(ok=False, error="execution_refused", text=<refusal explanation>)`. Don't crash.
   - On success: format text as:

     ```
     $ <cmd>
     exit=<n>
     stdout:
     <stdout, truncated to 2 KB>
     stderr:
     <stderr, truncated to 1 KB>
     ```

     Truncation prevents the LLM payload from exploding on a large `ls -R` or similar.
   - Return `AgentResult(text=formatted, ok=(exit_code == 0), model=None, tokens_in=0, tokens_out=0, latency_ms=…, metadata={"exit_code": n, "argv": argv})`.

3. **Why `composer=False`:** the command output is data the persona summarizes; it isn't the user-facing response.

4. Tests in `tests/test_agents_execution.py` (~6):
   - With `metadata.exec_command="echo hi"`: `ok=True`, text contains `$ echo hi`, exit=0, stdout `hi`.
   - With `metadata.exec_command="cat /etc/passwd"`: `ok=False, error="execution_refused"`.
   - No command anywhere: `ok=False, error="execution_no_command"`.
   - Fenced code block fallback: `input.message = "please run ```sh\nls\n```"`: agent extracts `ls` and runs.
   - Exit-code-nonzero command: `ok=False`, but no `error` (it ran; it just failed). Caller can read `metadata.exit_code`.
   - Truncation: large stdout (>2 KB) is truncated with a marker.

**Files added:** `src/ubongo/agents/execution.py`, `tests/test_agents_execution.py`.

### 11d — Repair Agent + runner wiring

**Purpose:** Phase 11's minimum-viable recovery. The runner calls the Repair Agent on `agent_failed`, which decides how to retry (Phase 11: single-retry with model fallback). The retry runs synchronously inside the runner so the trace stays in one workflow_run row.

**Tasks:**

1. Create `src/ubongo/agents/repair.py`:

   ```python
   class RepairAgent:
       name = "repair"
       role = "detects and recovers failed agent runs (single-retry v0.1)"
       composer = False
       default_model = ""

       def __init__(self) -> None:
           cfg = load_config()
           self._fallback_models = cfg.get("agents", {}).get("repair", {}).get(
               "fallback_models", {
                   "coding": cfg["models"].get("default"),
                   "architect": cfg["models"].get("default"),
                   "operator": cfg["models"].get("default"),
                   "casual": cfg["models"].get("casual"),
                   "research": cfg["models"].get("default"),
                   "evaluator": cfg["models"].get("default"),
                   "critic": cfg["models"].get("default"),
               }
           )

       def plan_retry(self, failed_agent_name: str, original_result: AgentResult,
                      input: AgentInput) -> dict | None:
           """Phase 11 v0.1: return {"model": <fallback>} or None for no retry.

           Phase 13 will return a richer recovery plan: replace_with_peer,
           rollback, skip, etc.
           """
   ```

2. `plan_retry(...)` logic v0.1:
   - If `failed_agent_name == "memory"`: return `None`. Memory writes are not retried at the runner level; Phase 13 will handle DB-side rollback differently.
   - If `failed_agent_name == "execution"`: return `None`. Sandbox refusals are by design, not transient.
   - If `original_result.error in {"persona_llm_error", "research_llm_error", "evaluator_llm_error", "critic_llm_error", "coding_llm_error"}`: return `{"model": fallback_models.get(failed_agent_name)}` — retry once with the configured fallback.
   - Otherwise: return `None`.

3. **Runner wiring** (`src/ubongo/runner.py`):
   - After `events.dispatch("agent_failed", ...)` (currently runner.py:163), look up `repair = self.registry.get("repair")`. If present and `repair.plan_retry(...)` returns a non-None plan AND we haven't retried this agent already in this workflow:
     - Replace `agent.default_model` for the retry via a transient `AgentInput.metadata["override_model"]` (each affected agent reads this; Phase 11 plumbs it through PersonaAgent / CodingAgent / ResearchAgent / EvaluatorAgent / CriticAgent — small additions where each agent calls `complete(... model=input.metadata.get("override_model") or self.default_model)`).
     - Re-execute the agent ONCE. Persist a SECOND `agent_runs` row for the retry attempt with `retried=1` on the column.
     - If retry succeeds, replace `result` with the retry result, append text to `prior_findings`, advance `last_composer_result` if applicable.
     - If retry also fails: dispatch `agent_failed` again, leave `any_failure=True`, continue to next agent.
   - Track per-workflow retries in a `retried_agents: set[str]` local so the same agent isn't retried twice.

4. **Schema change**: `agent_runs.retried INTEGER NOT NULL DEFAULT 0`. Migration helper in `store.bootstrap()` (mirror the Phase 9 `_migrate_workflow_runs_in_progress` pattern). `store.append_agent_run` gains a `retried: bool = False` kwarg.

5. **`agents.repair.fallback_models`** in `settings.yaml`. Default: every per-agent fallback is `models.default` except `casual` which stays on `models.casual` (cheap voice; switching to Sonnet for a casual reply is overkill).

6. Tests in `tests/test_agents_repair.py` (~6):
   - `plan_retry("memory", ...)` returns None.
   - `plan_retry("execution", ...)` returns None.
   - `plan_retry("coding", AgentResult(error="coding_llm_error"))` returns `{"model": <default>}`.
   - `plan_retry("research", AgentResult(error="some_unknown_error"))` returns None.
   - Fallback model mapping respects `settings.yaml` overrides.

7. Tests in `tests/test_runner.py` (modified, +3):
   - Single failing agent + retry succeeds: `agent_runs` shows two rows for that agent, second has `retried=1`, `WorkflowResult.ok` reflects the retry.
   - Single failing agent + retry also fails: two rows, `agent_failed` dispatched twice, `WorkflowResult.ok=False`.
   - `memory` failure: no retry (only one `agent_runs` row).

**Files added:** `src/ubongo/agents/repair.py`, `tests/test_agents_repair.py`.
**Files modified:** `src/ubongo/runner.py` (retry loop), `src/ubongo/memory/schema.sql` (+`retried` column), `src/ubongo/memory/store.py` (+migration shim + `retried` kwarg), `src/ubongo/agents/personas.py` + `src/ubongo/agents/coding.py` + `src/ubongo/agents/research.py` + `src/ubongo/agents/evaluator.py` + `src/ubongo/agents/critic.py` (read `override_model` from input.metadata; one-line change per file), `config/settings.yaml` (`+agents.repair.fallback_models`), `tests/test_runner.py`.

### 11e — `/exec <cmd>` REPL command

**Purpose:** Direct sandbox path for debugging. No master.handle, no workflow_runs, no queue, no vault.

**Tasks:**

1. **Parser** in `repl.py`:

   ```python
   def _parse_exec_command(line: str) -> str | None:
       """Returns the command body from `/exec <cmd>` (everything after the
       command word). None if `/exec` was typed with no argument."""
   ```

   Preserves the rest of the line verbatim (quotes, spaces) so `/exec "echo hello world"` and `/exec echo hello world` both work — the user can pre-quote or not.

2. **Renderer** `_render_exec(cmd)`:
   - Call `sandbox.run_constrained(cmd, timeout=10)`. Catch `SandboxRefused` and render `Refused: <reason>`. Catch any other exception and render `Error: <exc-class>: <msg-truncated>`.
   - On success, render the same shape Execution Agent uses:

     ```
     $ <cmd>
     exit=<n>  (<latency_ms>ms)
     stdout:
     <stdout>
     stderr:
     <stderr>
     ```

3. Wire `/exec` into the slash dispatcher. `_HELP_COMMANDS` updated.

4. Tests in `tests/test_repl_exec.py` (~4):
   - `_parse_exec_command("/exec echo hello")` returns `"echo hello"`.
   - `_parse_exec_command("/exec")` returns `None`.
   - `_render_exec("echo hi")` includes `$ echo hi`, `exit=0`, `stdout:`, `hi`.
   - `_render_exec("rm -rf /")` returns `Refused: …`.

**Files modified:** `src/ubongo/repl.py`, `tests/test_repl_exec.py` (new).

### 11f — `coding_session` workflow lit up

**Purpose:** When the classifier routes a turn to `coding_session`, the Coding Agent produces the code and the Architect Persona wraps it in commentary (tradeoffs, what was assumed).

**Tasks:**

1. **`config/workflows.yaml`** update:

   ```yaml
   coding_session:
     agents: ["coding", "architect"]
     mode: sequential
     evaluate: true

   execution_session:        # declared but NOT auto-routed in Phase 11
     agents: ["execution", "architect"]
     mode: sequential
     evaluate: false
   ```

2. **Routing rule** (`config/routing.yaml`): the existing `intent: coding -> coding_session` rule (line 7) keeps working — no change. Phase 11's job is to populate the agent list, not the routing.

3. **Why "evaluate: true" for coding_session:** code is a high-correctness, low-warmth output. The Evaluator's "completeness + hallucination signals" rubric maps directly. Borderline-Critic kicks in for muddy spec → Critic challenges → Architect re-explains.

4. **Why "execution_session" declared but not auto-routed:** auto-running a shell command on classifier confidence alone is a Phase-15-after-approval-gate scenario, not Phase 11. The workflow exists for forward-compat with Phase 12 (a future `/mode execution_session` debug command) and integration tests.

5. Tests in `tests/test_router.py` (modified, +2):
   - `workflow_agents("coding_session")` returns `("coding", "architect")`.
   - `workflow_agents("execution_session")` returns `("execution", "architect")`.

**Files modified:** `config/workflows.yaml`, `tests/test_router.py`.

### 11g — STATUS + smoke playbook Phase 11 section

**Tasks:**

1. Append Phase 11 section to `tests/manual/smoke_test.md` with these scenarios:

   | # | Scenario | Steps | Expected |
   | --- | --- | --- | --- |
   | 11.1 | Coding Agent | `ubongo send "write a Python function that reverses a list" --persona architect` (auto via classifier picks `coding_session`) | Response contains a `def reverse_list(lst):` style function with type hints and a usage example. `agent_runs` rows: `coding`, `architect`, `evaluator`, `memory`. `workflow.agents == ["coding","architect"]`. |
   | 11.2 | `/exec` happy path | REPL: `/exec echo hello world` | Block with `$ echo hello world`, `exit=0`, `stdout:\nhello world`. |
   | 11.3 | `/exec` refused — disallowed program | REPL: `/exec rm -rf /` | `Refused: program 'rm' not in allowlist`. No filesystem mutation. |
   | 11.4 | `/exec` refused — shell metachar | REPL: `/exec ls; cat /etc/passwd` | `Refused: shell metacharacter ';' rejected`. |
   | 11.5 | `/exec` refused — path traversal | REPL: `/exec cat ../../etc/passwd` | `Refused: path traversal in argument`. |
   | 11.6 | Repair single-retry | Run a forced-failure test: `uv run pytest tests/test_runner.py::test_repair_retries_failing_agent_once` | Pass. Two `agent_runs` rows for the failing agent; second has `retried=1`; final `WorkflowResult.ok=True`. |
   | 11.7 | Repair gives up after retry also fails | `uv run pytest tests/test_runner.py::test_repair_gives_up_after_second_failure` | Pass. Two rows, both `outcome='failure'`, `WorkflowResult.ok=False`. |
   | 11.8 | `/agents` includes new workers | REPL: `/agents` | Header + rows now include `coding`, `execution`, `repair` alongside the Phase-10 set (10 total). |
   | 11.9 | `/exec` not in workflow_runs | After 11.2: `sqlite3 data/ubongo.db "SELECT COUNT(*) FROM workflow_runs WHERE classification LIKE '%exec%'"` | 0. `/exec` is debug-only and does not create a workflow_run row. |
   | 11.10 | Help line includes `/exec` | REPL: `/foo` | Help banner lists `/exec` between `/trace` and `/reload`. |
   | 11.11 | Pytest passes | `uv run pytest tests/` | All green (~300 expected after Phase 11: Phase-10's 273 + 5 coding + 10 sandbox + 6 execution + 6 repair + 4 /exec - some existing test deltas). |

2. Update scenario 1.7 help-line expected text: add `/exec` between `/trace` and `/reload`.

3. Update Phase 9 scenario 9.3 + Phase 10 scenario 10.4 expected `/agents` rows: now ten entries (`architect`, `casual`, `coding`, `critic`, `evaluator`, `execution`, `memory`, `operator`, `repair`, `research`).

4. Update `STATUS.md`: Phase 11 row → Complete (date); Overall paragraph rewritten; LOC count bumped.

**Files modified:** `tests/manual/smoke_test.md`, `STATUS.md`.

## Final file tree after Phase 11

```text
src/ubongo/
  sandbox.py                            (new — run_constrained, allowlist, refusal)
  agents/
    coding.py                           (new — CodingAgent, composer=True)
    execution.py                        (new — ExecutionAgent, composer=False)
    repair.py                           (new — RepairAgent.plan_retry, single-retry policy)
    personas.py                         (modified — read override_model from metadata)
    research.py                         (modified — same)
    evaluator.py                        (modified — same)
    critic.py                           (modified — same)
  runner.py                             (modified — retry loop on agent_failed; per-workflow retried set)
  repl.py                               (modified — /exec command + help line)
  memory/
    schema.sql                          (modified — +agent_runs.retried)
    store.py                            (modified — migration shim, +retried kwarg)
config/
  skills/
    constrained-bash/
      SKILL.md                          (new — risk: medium, reversibility: irreversible)
      prompts/
        run.md                          (new — Execution Agent's how-to)
  workflows.yaml                        (modified — coding_session agents; +execution_session)
  settings.yaml                         (modified — +agents.coding.max_tokens; +agents.repair.fallback_models)
tests/
  test_agents_coding.py                 (new ~5)
  test_sandbox.py                       (new ~10)
  test_agents_execution.py              (new ~6)
  test_agents_repair.py                 (new ~6)
  test_repl_exec.py                     (new ~4)
  test_runner.py                        (modified — +3 retry tests)
  test_router.py                        (modified — +2 workflow tests)
  test_skills_discovery.py              (modified — +1 constrained-bash assertion)
Plans/
  phase-11-remaining-workers.md         (new — this file)
STATUS.md                               (modified)
tests/manual/smoke_test.md              (modified — Phase 11 section + 1.7/9.3/10.4 tweaks)
```

Untouched: `classifier.py`, `delivery/queue.py`, `memory/compaction.py`, `memory/vault.py`, `agents/memory.py`, `agents/base.py`, `master.py` (no master changes — coding_session uses the existing workflow machinery), `governance/decision.py`, `oneshot.py`, `events.py`, `config/personas/*`, `config/UBONGO.md`, `config/routing.yaml` (existing `intent: coding -> coding_session` rule keeps working unchanged).

## Open questions to confirm before I start

1. **Sandbox allowlist scope (recommended set).** Phase 11 allows: `ls, pwd, echo, cat, head, tail, wc, grep, find, git, python, python3, pip, uv, pytest, sqlite3, true, false`. No `rm`, `mv`, `cp`, `mkdir`, `chmod`, `curl`, `ssh`, `npm`, `make`, `docker`. The Coding Agent works with text; the Execution Agent inspects state. v0.1 is read-mostly. Phase 15 will add write-allowed commands behind the approval gate. OK with this list, or add/remove?
2. **No pipes / redirects in the wrapper (recommended).** Phase 11 rejects `;`, `|`, `&`, `` ` ``, `$(`, `>`, `<` at the argument-tokenize level. If the agent needs `ls | grep foo`, it runs `ls` then `grep foo <captured-output>` as two calls. This is restrictive but it avoids a whole class of injection that requires per-shell-flavor parsing to do safely. OK?
3. **Repair v0.1 = single-retry with model fallback (recommended).** No peer-agent replacement; no rollback. The `_fallback_models` mapping is configurable via `settings.yaml` and defaults to `models.default` for everything except casual (stays on cheap). Phase 13 broadens. OK?
4. **`override_model` plumbed via `AgentInput.metadata` (recommended).** Each LLM-calling agent reads `input.metadata.get("override_model") or self.default_model` when building its `complete(...)` call. Small additive change per agent. Alternative: add a `model:` kwarg to `Agent.run`. I lean metadata — keeps the protocol stable. OK?
5. **`agent_runs.retried` boolean column with a schema migration (recommended).** Lets `/trace` distinguish first-attempt vs retry rows cleanly. Migration mirrors Phase 9's `_migrate_workflow_runs_in_progress` shim. Alternative: encode "retry" in the output JSON only. I lean column — it's a first-class trace concept and Phase 13 will lean on it harder. OK?
6. **`coding_session` workflow has BOTH `coding` and `architect` as composers (recommended).** Last-composer-wins (the runner's existing rule from Phase 10) makes the architect's text the final response. Coding's text feeds into architect via `prior_findings`. Alternative: `coding_session: ["coding"]`. I lean both — architect adds the "why this code" + tradeoffs commentary that v0.1 wants. OK?
7. **`execution_session` declared but not auto-routed (recommended).** Putting an auto-route for "intent: execution" in `routing.yaml` would mean classifier confidence alone could trigger shell execution. That belongs behind Phase 15's approval gate, not Phase 11. The workflow exists for `/mode execution_session` (Phase 12 debug) and tests. OK?
8. **`/exec` bypasses the entire `master.handle` flow (recommended).** No workflow_run row, no governance decision, no enqueue, no vault. It's debug. Alternative: route through Execution Agent + master so the trace covers it. I lean bypass — the manual scenario in the spec calls it "debug only" and the trace machinery exists for production turns. OK?
9. **Coding model = `settings.yaml::models.coding`** (already set to `openrouter/anthropic/claude-sonnet-4.5`, same as architect). No new model entry needed. OK?
10. **No `master.py` changes (recommended).** Coding/Execution/Repair plug into the existing runner + workflows + registry. Master is unaffected. Smaller diff is better; revisit only if Phase 13 / Phase 15 require it. OK?

If you don't push back, I'll go with the defaults above.

## Definition of done for Phase 11

- 8 commits on `phase-11-remaining-workers` (Plan + 11a–11g). Push the branch and open the draft PR immediately after the Plan commit.
- Smoke scenarios 11.1–11.11 pass; 11.11 pytest green.
- New tests: `test_agents_coding.py` (~5), `test_sandbox.py` (~10), `test_agents_execution.py` (~6), `test_agents_repair.py` (~6), `test_repl_exec.py` (~4). Existing tests still pass with the updates listed.
- `tests/manual/smoke_test.md` Phase 11 section appended; help-line + `/agents` tweaks applied in earlier sections.
- `STATUS.md` Phase 11 row → Complete; "Overall" paragraph refreshed; LOC count bumped.
- Branch handed to you for merge. **Don't merge.**

---

(Verified: `origin/main` matches local `main` at `beca817`. Phase 10 fully merged; smoke patches landed. Branch `phase-11-remaining-workers` exists locally; not yet pushed.)
