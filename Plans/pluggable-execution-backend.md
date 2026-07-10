---
I am installing Hermes Agent and I see the Following

Select terminal backend:
  ↑↓ navigate  ENTER/SPACE select  ESC cancel

   (○) Local - run directly on this machine (default)
   (○) Docker - isolated container with configurable resources
   (○) Modal - serverless cloud sandbox
   (○) SSH - run on a remote machine
 → (○) Daytona - persistent cloud development environment
   (○) Singularity/Apptainer - HPC-friendly container
   (●) Keep current (local)


Select platforms to configure:
  ↑↓ navigate  SPACE toggle  ENTER confirm  ESC cancel

 → [ ] 📱 Telegram  (not configured)
   [ ] 💼 Slack  (not configured)
   [ ] 🔐 Matrix  (not configured)
   [ ] 💬 Mattermost  (not configured)
   [ ] 📲 WhatsApp  (not configured)
   [ ] 📡 Signal  (not configured)
   [ ] 📧 Email  (not configured)
   [ ] 📱 SMS (Twilio)  (not configured)
   [ ] 💬 DingTalk  (not configured)
   [ ] 🪽 Feishu / Lark  (not configured)
   [ ] 💬 WeCom (Enterprise WeChat)  (not configured)
   [ ] 💬 WeCom Callback (Self-Built App)  (not configured)
   [ ] 💬 Weixin / WeChat  (not configured)
   [ ] 💬 BlueBubbles (iMessage)  (not configured)
   [ ] 🐧 QQ Bot  (not configured)
   [ ] 💎 Yuanbao  (not configured)
   [ ] 🎮 Discord  (not configured)
   [ ] 💬 Google Chat  (not configured)
   [ ] 🏠 Home Assistant  (not configured)
   [ ] 💬 IRC  (not configured)
   [ ] 💚 LINE  (not configured)
   [ ] 🔔 ntfy  (not configured)
   [ ] 📱 iMessage via Photon  (not configured)
   [ ] 🔒 SimpleX Chat  (not configured)
   [ ] 💼 Microsoft Teams  (not configured)


 Choose how the gateway should run in the background:
  ↑↓ navigate  ENTER/SPACE select  ESC cancel

 → (●) User service (no sudo; best for laptops/dev boxes; may need linger after logout)
   (○) System service (starts on boot; requires sudo; still runs as your user)
   (○) Skip service install for now


Tools for 🖥️  CLI
  ↑↓ navigate  SPACE toggle  ENTER confirm  ESC cancel

 → [✓] 🔍 Web Search & Scraping  (web_search, web_extract)
   [✓] 🌐 Browser Automation  (navigate, click, type, scroll)
   [✓] 💻 Terminal & Processes  (terminal, process)
   [✓] 📁 File Operations  (read, write, patch, search)
   [✓] ⚡ Code Execution  (execute_code)
   [✓] 👁️  Vision / Image Analysis  (vision_analyze)
   [✓] 🎬 Video Analysis  (video_analyze (requires video-capable model))
   [✓] 🎨 Image Generation  (image_generate)
   [ ] 🎬 Video Generation  (video_generate (text-to-video + image-to-video))
   [ ] 🐦 X (Twitter) Search  (x_search (requires xAI OAuth or XAI_API_KEY))
   [✓] 🧠 Mixture of Agents  (mixture_of_agents)
   [✓] 🔊 Text-to-Speech  (text_to_speech)
   [✓] 📚 Skills  (list, view, manage)
   [✓] 📋 Task Planning  (todo)
   [✓] 💾 Memory  (persistent memory across sessions)
   [✓] 🧩 Context Engine  (runtime tools from the active context engine)
   [✓] 🔎 Session Search  (search past conversations)
   [✓] ❓ Clarifying Questions  (clarify)
   [✓] 👥 Task Delegation  (delegate_task)
   [✓] ⏰ Cron Jobs  (create/list/update/pause/resume/run, with optional attached skills)
   [✓] 📨 Cross-Platform Messaging  (send_message)
   [ ] 🏠 Home Assistant  (smart home device control)  [no API key]
   [ ] 🎵 Spotify  (playback, search, playlists, library)
   [ ] 🤖 Yuanbao  (group info, member queries, DM)
   [✓] 🖱️  Computer Use (macOS)  (background desktop control via cua-driver)




  Choose a provider:
  ↑↓ navigate  ENTER/SPACE select  ESC cancel

 → (●) Local Browser [★ recommended · free] — Headless Chromium, no API key needed
   (○) Nous Subscription (Browser Use cloud) [subscription] — Managed Browser Use billed to your subscription  ★ via Nous Portal (login on select)
   (○) Camofox [free · local] — Anti-detection browser (Firefox/Camoufox)
   (○) Browser Use [paid] — Cloud browser with remote execution
   (○) Browserbase [paid] — Cloud browser with stealth and proxies
   (○) Firecrawl [paid] — Cloud browser with remote execution
   (○) Skip — keep defaults / configure later



Firecrawl


This should give you some ideas for Ubongo
---

# Pluggable execution backend (the Hermes "terminal backend" idea, Ubongo-shaped)

Status: **DRAFT for revision.** Not yet sequenced into a phase, not approved. The
version/branch slot is an open decision (see below). Origin: the Hermes Agent installer's
first menu (Local / Docker / Modal / SSH / Daytona / Singularity) — "where does the shell
actually run" as a configurable backend. This plan adopts the *one* idea worth stealing and
explicitly rejects the rest (see Non-goals).

Work classification: brownfield (a behavior-identical split of the existing `run_constrained`
chokepoint; every current sandbox test must pass unchanged). Rigor mode: Strict, minimum —
the sandbox is trust-spine (ADR-0005), and a misrouted executor is a security regression.

## The one-sentence claim

`run_constrained` in [src/ubongo/sandbox.py](../src/ubongo/sandbox.py) is the single chokepoint
for everything that runs a process on the machine. Today it both **decides whether a command is
allowed** and **runs it as a local subprocess**. Those are two jobs. Split them: keep the safety
contract in the module (it is policy; ADR-0005 says it lives where the LLM cannot rewrite it),
and put the *"where it runs"* half behind an `Executor` seam with `LocalExecutor` as the default
and only required implementation. Everything else is additive.

## Why this seam, and why it is not ADR-0017 again

ADR-0017 (the Podman + nftables envelope) wraps **the whole Ubongo process** on a Linux host so
egress is enumerable. This plan is a different layer: it routes **an individual already-validated
command** to a chosen executor. The two compose cleanly and do not overlap — 0017 bounds what the
process can reach; this decides which host/sandbox each sandboxed command lands in. A `git grep`
in a remote checkout, a `pytest` run offloaded to a beefier box, a command run inside a throwaway
container: same `SandboxResult` contract, same allowlist gate, different executor.

Critically this does **not** reopen the no-Docker architecture posture. As with 0017, any non-local
backend is *deployment-time, optional, zero core dependency* — the test suite runs on
`LocalExecutor` alone, and the heavy SDK (paramiko / podman bindings / etc.) imports lazily behind
an optional extra, exactly like `streamlit` / `mcp` / `telegram` do today.

## The shape

A two-method protocol, and the validation gate stays put:

```python
# sandbox.py — unchanged responsibility: policy. Runs BEFORE any executor sees argv.
def validate_command(cmd) -> argv      # allowlist + metachars + path-root checks (today's code)

# new: the seam
class Executor(Protocol):
    def resolve(self, program: str) -> str | None      # program -> absolute path on THAT host
    def run(self, argv, *, cwd, env, timeout) -> SandboxResult

class LocalExecutor:   # today's behavior, lifted verbatim
    # owns _PROGRAM_PATHS (shutil.which on the local host), the empty-PATH child,
    # subprocess.run with the tight env, the timeout/FileNotFound/Exception handling.

def run_constrained(cmd, *, timeout=None, executor=None) -> SandboxResult:
    argv = validate_command(cmd)            # <-- policy, always in-process, never skipped
    ex = executor or _active_executor()     # config-selected, default LocalExecutor
    return ex.run(argv, cwd=..., env=..., timeout=...)
```

Three things move out of the module function and into `LocalExecutor`, because they are
local-host facts, not policy: `_PROGRAM_PATHS` (resolution via `shutil.which`), the empty-PATH
child trick, and `subprocess.run`. Three things stay in the module because they are the contract:
`ALLOWED_COMMANDS`, the metachar/path checks, and the `SandboxResult` dataclass.

## What changes

1. **Refactor sandbox.py into validate (policy) + `LocalExecutor` (mechanism).**
   Behavior-identical. The existing sandbox tests must pass unchanged with zero edits — that is
   the proof the refactor is clean.
2. **`Executor` protocol + a registry** keyed by name. `sandbox.executor` in `settings.yaml`,
   default `"local"`. Unknown/misconfigured executor **fails closed** (refuse the run) — it never
   silently falls back to local, because a misread config that downgrades isolation is a security
   regression.
3. **One proof-of-concept second backend** behind an optional extra, to prove the abstraction
   holds across a real process boundary. Which one is the key open decision below.
4. **Path-root semantics made host-relative.** Today `_check_paths` enforces "absolute args
   resolve inside `_REPO_ROOT`" against the *local* filesystem. A remote/container executor has a
   *different* root. The validation gate must take the target root as a parameter so a remote
   executor validates against the remote checkout, not the local one. This is the subtlest part
   of the change and the easiest to get wrong.
5. **Non-local execution is a governable capability.** Routing a command off the local machine is
   higher-consequence than a local `ls`. At minimum the chosen executor is recorded in the run
   trace. Stronger (and the right long-term home): a non-local executor is a *capability class*
   gated through the grant registry (ADR-0019) — granted once, then narrates-and-proceeds. This
   is what makes the feature fit the trust protocol instead of bypassing it.
6. **An ADR** recording: the validate/execute split, that policy stays in-module (extends 0005),
   the layer distinction from 0017, and the optional-dependency + platform-asymmetry posture.

## Non-goals (the Hermes ideas this plan deliberately rejects)

- **Not** the 26-platform channel menu. The additive-channel seam already makes channels cheap;
  breadth for its own sake is the multi-channel sprawl CLAUDE.md rules out. (If a privacy-first
  channel is wanted next, that is a separate channel plan — Matrix/Signal, not this.)
- **Not** per-tool provider menus (browser providers, video gen, X search). "New tools default to
  CLI scripts through constrained-bash; first-class tools require justification" stays the posture.
- **Not** `mixture_of_agents` / `delegate_task` as model-callable tools. The Master Agent already
  owns orchestration; exposing it as a tool would invert that.
- **Not** Modal/Daytona/Browserbase-style paid cloud sandboxes in v1 of this. The seam allows them
  later; building them now is scope the user has not asked for.

## QA test plan

### Acceptance criteria (exit = all checked)

- [ ] **AC-1 Behavior-identical refactor.** Every existing sandbox test passes unchanged;
      `LocalExecutor` returns a byte-identical `SandboxResult` for the current test corpus. No
      caller (skills layer, Execution agent) changes.
- [ ] **AC-2 Policy never skipped.** `validate_command` runs in-process before dispatch for
      *every* backend. A command that fails the allowlist/metachar/path checks is refused
      identically regardless of configured executor, and nothing leaves the process.
- [ ] **AC-3 Config-selected, fail-closed.** `sandbox.executor` defaults to `local`; an unknown
      or misconfigured executor name raises `SandboxRefused` (never a silent local fallback).
- [ ] **AC-4 Dependency isolation.** Full suite green with only `LocalExecutor`; the
      second-backend SDK imports lazily and the suite passes without it installed (mirror
      streamlit/mcp/telegram).
- [ ] **AC-5 Second backend round-trips.** A validated command runs on the chosen target and
      returns a same-shape `SandboxResult`; timeout is enforced on that target; path-root checks
      validate against the *target's* root, not the local one.
- [ ] **AC-6 Governed + traced.** The executor used is persisted in the run trace; a non-local
      executor is gateable (trace-only at minimum, grant-gated per ADR-0019 as the stretch).
- [ ] **AC-7 ADR landed.** ADR records the split, the 0005/0017 relationships, and the posture.

### Smoke additions

A new section in `tests/manual/smoke_test.md`: run an Execution-mode turn with `executor: local`
(baseline), then flip to the second backend and confirm the same turn produces an equivalent
result and a trace row naming the backend; confirm a disallowed command is refused before either
backend runs.

## Open decisions (need your call before sequencing)

1. **Which second backend proves the seam?**
   - **SSH (recommended):** most different from local, so it stress-tests the abstraction hardest
     (real network + remote process + remote path root). Matches the genuinely useful "offload to
     a beefier box" case. Cost: paramiko/openssh dependency, remote-root path semantics.
   - **Podman container:** reuses ADR-0017's existing Podman commitment, strongest isolation,
     Linux-first. Cost: container lifecycle, image build, macOS gap.
   - I lean SSH for the proof and Podman as a fast-follow, but this is yours to set.
2. **Version/branch slot.** Trust-protocol phases run 00–07 (06 standing jobs, 07 contract/identity
   remain). Is this a v0.6 opener, a deferred candidate after 07, or folded in earlier? Branch name
   drives the version (`v0.MAJOR.NN`), so this decides the filename rename too.
3. **Governance depth in v1.** Trace-only (cheap, ships now) vs grant-gated capability class
   (correct end state, depends on the grant registry being in the shape Phase 05 left it).
