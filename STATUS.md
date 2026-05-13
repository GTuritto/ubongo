# Ubongo — Implementation Status

Last updated: 2026-05-12

## Overall

Phases 0–8 merged. Phase 9 (First Workers: Research + Memory) complete on `phase-9-research-memory` branch, awaiting user merge. Real worker agents enter the system: `MasterAgent.execute` is a thin delegate to `WorkflowRunner.execute`, which dispatches the agents listed in `workflow.agents` in sequential order, threads each agent's output forward via `AgentInput.prior_findings`, and writes one `agent_runs` row per dispatch. `config/workflows.yaml` declares the agents + mode per workflow name; `research_brief` runs `["research", "persona:architect"]` and other workflows stay single-persona. The Research Agent retrieves cross-conversation messages by keyword overlap and greps recent daily-note files; the Memory Agent owns the assistant-message write and the `after_send` vault projection (single-writer rule; soft enforcement in production, strict-mode pytest fixture verifies). `workflow_runs` rows are INSERTed with `outcome='in_progress'` before execute and UPDATEd after, so the runner can FK-link `agent_runs` immediately. `/agents` lists the registered workers. v0.1 scope is multi-agent + self-improving + CLI; see [UBONGO_BUILD.md](UBONGO_BUILD.md).

## Phase Tracker

| # | Tier | Phase | Branch | Status |
| --- | --- | --- | --- | --- |
| 0 | Foundation | Skeleton | `phase-0-skeleton` | Complete (2026-05-09) |
| 1 | Foundation | CLI REPL + One-Shot (echo) | `phase-1-cli-echo` | Complete (2026-05-09) |
| 2 | Foundation | LLM Integration | `phase-2-llm` | Complete (2026-05-10) |
| 3 | Foundation | Tone Classifier + Auto Routing | `phase-3-classifier` | Complete (2026-05-10) |
| 4 | Foundation | SQLite Memory + Compaction | `phase-4-memory` | Complete (2026-05-10) |
| 5 | Foundation | Markdown Vault Projection | `phase-5-vault` | Complete (2026-05-10) |
| 6 | Foundation | Skills + Progressive Disclosure | `phase-6-skills` | Complete (2026-05-11) |
| 7 | Foundation | Minimal Outbound Queue | `phase-7-queue` | Complete (2026-05-12) |
| 8 | Multi-Agent | Master Agent | `phase-8-master` | Complete (2026-05-12) |
| 9 | Multi-Agent | First Workers (Research + Memory) | `phase-9-research-memory` | Complete (2026-05-12) |
| 10 | Multi-Agent | Evaluator + Critic + Persona Agents | `phase-10-evaluator-critic` | Not started |
| 11 | Multi-Agent | Coding + Execution + Repair Agents | `phase-11-remaining-workers` | Not started |
| 12 | Multi-Agent | Execution Modes (all six) | `phase-12-modes` | Not started |
| 13 | Self-Healing | Repair Agent Activated | `phase-13-repair` | Not started |
| 14 | Governance | Risk + Confidence Scoring | `phase-14-governance-rules` | Not started |
| 15 | Governance | Approval Gates + Sandboxing | `phase-15-approval-sandbox` | Not started |
| 16 | Self-Improvement | Variant Generation | `phase-16-variants` | Not started |
| 17 | Self-Improvement | Sandboxed Evaluation + Fitness | `phase-17-evaluation` | Not started |
| 18 | Self-Improvement | GP Loop (autonomous) | `phase-18-gp-loop` | Not started |
| 19 | Self-Improvement | GP Targets Expanded + Promotions | `phase-19-promotions` | Not started |
| 20 | Wiki Memory | Embeddings + Graph | `phase-20-embeddings-graph` | Not started |
| 21 | Polish | Bidirectional Vault Sync + Audit | `phase-21-vault-sync-audit` | Not started |

Each phase is built on its own branch. Don't start Phase N+1 until Phase N's testing plan and smoke test pass and the branch is merged into `main`.

## Lines of Code

3513 / ~15,000 soft target (excluding tests). Phases 0-9 adds: Agent protocol + AgentInput/AgentResult dataclasses (agents/base.py), Research Agent with cross-conversation keyword retrieval + daily-note grep (agents/research.py), Memory Agent owning assistant-message + vault writes under a ContextVar single-writer token (agents/memory.py), PersonaAgent wrapper turning each persona into an Agent (agents/personas.py), WorkflowRunner.execute for sequential mode with agent_runs writes + agent_started/_completed/_failed dispatch (runner.py), workflows.yaml declaring agents+mode per workflow name, router.route_workflow/workflow_agents/workflow_persona helpers, workflow_runs INSERT-before/UPDATE-after lifecycle with schema migration shim for the 'in_progress' outcome, /agents REPL command.

## v0.1 Acceptance Criteria

- [ ] CLI REPL responds; one-shot command runs and exits.
- [ ] Manual `/architect`, `/operator`, `/casual` commands work and feel different.
- [ ] In `/auto` mode, persona is selected automatically and feels mostly right.
- [ ] You can correct auto-selection with a slash command.
- [ ] `UBONGO.md` is loaded for every persona; editing it changes behavior across all personas after `/reload`.
- [ ] Conversation context persists across CLI restarts within a session.
- [ ] New session starts after 30 minutes of inactivity.
- [ ] Compaction kicks in past the configured threshold; older history replaced by a summary in recall; summary persisted and not regenerated.
- [ ] Daily notes write to the Obsidian vault and render correctly.
- [ ] `summarize-conversation` skill works via `/summary`. Skill bodies not loaded until activation.
- [ ] `/reload` picks up edits to `UBONGO.md`, personas, and skill metadata without restart.
- [ ] Every outbound message goes through `notification_queue`.
- [ ] Master Agent classifies, plans, dispatches, governs, composes per turn; `/decisions` and `/trace` populated.
- [ ] All eight worker agents (Research, Coding, Evaluator, Repair, Memory, Critic, Execution, Persona) registered and dispatchable; `/agents` lists them.
- [ ] All six execution modes (sequential, parallel, competitive, collaborative, debate, speculative) selectable via `/mode`.
- [ ] Repair Agent recovers timeouts, parse errors, agent failures; rollbacks leave no partial state.
- [ ] Decision matrix returns auto / ask_clarification / require_approval / reject per `governance.yaml`; `governance_decisions` populated.
- [ ] `require_approval` flow prompts user; y/n/why all work; Execution Agent properly sandboxed.
- [ ] `/optimize <target>` generates variants; `/evaluate` produces a fitness leaderboard.
- [ ] GP loop runs autonomously when enabled; throttled; pauseable.
- [ ] `/improvements` lists pending promotions with diffs; approve/reject/rollback work; live-target swap takes effect.
- [ ] Semantic recall via `sqlite-vec` augments recency in `/recall`; vault-link graph queryable.
- [ ] File watcher ingests vault edits; conflicts gated by approval flow.
- [ ] Full `tests/manual/smoke_test.md` walkthrough passes end-to-end.
- [ ] Total project size stays under ~15,000 lines of Python (excluding tests).
- [ ] Each phase landed via its own branch and was merged to `main` only after user approval.

## Notes

Update this file as phases land. When a phase is merged to `main`, change its row from "Not started" → "Complete" and add a date.
