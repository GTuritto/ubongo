# Ubongo — Vision (Origin Document)

> **This document is the design exposition that v0.1 now realizes.**
>
> It captures the architectural arc — Master Agent orchestration, worker agents, parallel/debate/speculative execution, governance, Genetic Programming — that the project is built around. v0.1 implements most of it on a hand-rolled (no LangGraph) Python runtime, accessed through a local CLI; Telegram is the planned second channel in v0.2. A few items here remain explicitly out of v0.1 scope (multi-channel, multi-user, distributed deployment); see [UBONGO_BUILD.md](UBONGO_BUILD.md) for the precise scope, the 22-phase build plan, sub-phases, and per-phase testing plans.
>
> For the v0.1 scope, build phases, and acceptance criteria, see **[UBONGO_BUILD.md](UBONGO_BUILD.md)**.
> For setup and current status, see **[README.md](README.md)** and **[STATUS.md](STATUS.md)**.

---

## Conversation Summary

Overview

This document summarizes the conceptual architecture, design discussions, and technical direction for Ubongo, a personal experimental adaptive multi-agent assistant inspired by OpenClaw, Hermes, orchestrated cognitive runtimes, and persistent memory systems.

The discussion evolved from analyzing projects such as OpenClaw and Hermes into defining a lightweight, modular, orchestrated personal AI ecosystem capable of:

* Multi-agent orchestration
* Dynamic LLM routing
* Long-term memory
* Personality adaptation
* Workflow automation
* Self-healing execution
* Controlled runtime evolution
* Human governance and approval systems
* Parallel agent execution
* Wiki-first memory architecture
* Experimental Genetic Programming optimization

The system is envisioned less as a chatbot and more as:

* A personal adaptive AI mind
* A modular cognitive playground
* A persistent digital companion
* A mood-aware orchestration runtime

⸻

Initial Inspiration

OpenClaw

OpenClaw was discussed as an autonomous assistant ecosystem focused on:

* Persistent assistants
* Tool orchestration
* Multi-channel interaction
* Local/self-hosted deployments
* Agent workflows
* Memory systems
* MCP integration

Strengths identified:

* Large ecosystem
* Integration flexibility
* Multi-tool orchestration
* Strong community momentum

Weaknesses identified:

* Infrastructure-heavy
* Complex orchestration
* Security risks around autonomy
* Limited governance structure

⸻

Hermes Agent

Hermes was discussed as a more memory-centric and persistent personal assistant.

Strengths identified:

* Long-term memory orientation
* Self-improving workflows
* Lightweight deployment
* Persistent user context

OpenClaw and Hermes together inspired the idea of a broader orchestration runtime.

⸻

Core Architectural Direction

Main Insight

The future system should not be:

* One chatbot
* One model
* One personality
* One monolithic agent

Instead, it should behave like:

* A distributed cognitive runtime
* A digital organization of agents
* A governed orchestration platform

⸻

Master Agent Concept

The central architectural idea introduced was the concept of a Master Agent.

Responsibilities

The Master Agent acts similarly to:

* Engineering Manager
* Workflow orchestrator
* Cognitive scheduler
* Governance system

Responsibilities include:

* Task decomposition
* Agent creation and destruction
* Workflow coordination
* Risk evaluation
* Personality routing
* Conflict resolution
* Approval management
* Context management
* Model routing coordination

The Master Agent should:

* Spawn specialized agents dynamically
* Monitor agent execution
* Stop or replace agents
* Trigger retries and repairs
* Coordinate memory usage
* Evaluate user intent and tone

⸻

Specialized Worker Agents

The system should include specialized worker agents.

Examples discussed:

Agent Responsibility
Research Agent Search, retrieval, synthesis
Coding Agent Code generation and refactoring
Evaluator Agent Validate outputs and correctness
Repair Agent Recover failed workflows
Memory Agent Manage memory consolidation
Critic Agent Contrarian or brutal analysis
Execution Agent Tools, shell commands, APIs
Persona Agents Different communication styles

Key principle:

Worker agents should generally be disposable.

Durable intelligence should live in:

* Memory
* Workflows
* Policies
* Governance
* Knowledge systems

not in individual agents.

⸻

User Interaction Model

Initial Position

Initially, the idea was proposed that users should interact only with a stable user-facing persona.

Reasons:

* Maintain continuity
* Reduce chaos
* Avoid conflicting personalities
* Preserve memory consistency
* Simplify governance

⸻

Refined Position

The discussion evolved into a more advanced model:

Users should be able to interact directly with worker agents and specialized personas intentionally.

Examples:

Persona Purpose
Professional Work interactions
Architect Technical discussions
Critic Brutal analysis
Casual Friendly interactions
Adult/Private Informal mature interactions
Operator Fast execution mode
Researcher Long-form analysis

Key insight:

Different personalities should share:

* Same memory core
* Same governance layer
* Same orchestration runtime

but expose different behavioral layers.

⸻

Tone-Aware Routing

The system should adapt dynamically to:

* User tone
* Context
* Intent
* Risk
* Historical preferences

Examples:

Input Tone Routing Behavior
Formal Professional persona
Technical Architect/coding agents
Playful Casual or critic persona
Emotional Coaching/supportive persona
Adult/private Mature/private persona

Key principle:

Tone influences personality selection.

Risk influences governance.

⸻

Governance and Approval Model

A central realization was that autonomy must be governed.

The Manager Agent should decide when to:

* Proceed automatically
* Ask for clarification
* Request approval
* Escalate to the user
* Reject actions

Decision model proposed:

Decision = Intent + Risk + Confidence + Context + Preferences + Reversibility

Examples:

* Summaries may execute automatically
* Sending emails may require approval
* Destructive actions always require confirmation

⸻

Multi-Model Routing

A core requirement identified was the ability to use multiple LLMs simultaneously.

Examples:

Task Model Type
Intent classification Small fast model
Summarization Cheap model
Coding Strong coding model
Planning High reasoning model
Conversational interaction Personality-focused model
Retrieval ranking Embedding/reranker model

The system should dynamically select models based on:

* Cost
* Latency
* Capability
* Context size
* Privacy
* Reliability

LiteLLM was proposed as the routing abstraction layer.

⸻

Memory Architecture

Initial Memory Model

Layered memory was proposed:

Memory Type Purpose
Short-term Active context
Working Current workflows/goals
Episodic Historical interactions
Semantic Stable facts
Procedural Learned workflows
Preference User communication patterns
Reflective Lessons learned

⸻

Wiki-First Memory Architecture

A major improvement discussed was using an Obsidian-compatible Markdown wiki as the canonical memory layer.

Key insight:

The vector database should not be the source of truth.

The Markdown vault should be the durable human-readable memory.

Architecture:

Markdown Wiki (Canonical Memory)
        ↓
Git Versioning
        ↓
Embeddings Index
        ↓
Graph Relationships

Benefits:

* Human-readable memory
* Editable by users
* Git versioning
* Obsidian compatibility
* Graph relationships
* Long-term maintainability
* Reduced black-box behavior

Example vault structure:

/vault
  /people
  /projects
  /agents
  /workflows
  /preferences
  /daily
  /decisions
  /system

Key design decision:

Agents should not freely write to memory.

Memory updates should pass through a dedicated Memory Agent.

⸻

Parallel and Multithreaded Agent Execution

The discussion evolved into adding support for parallel agent execution.

Execution modes discussed:

Mode Description
Sequential One after another
Parallel Independent concurrent execution
Competitive Multiple agents solve same task
Collaborative Agents work on different subtasks
Debate Agents argue opposing views
Speculative Cheap agents start before stronger validation

Benefits:

* Faster execution
* Better answer quality
* Redundant validation
* Lower latency
* Better exploration

Risks:

Risk Mitigation
Race conditions Shared-state locking
Conflicting outputs Evaluator aggregation
Memory corruption Single-writer memory model
Cost explosion Budgets and cancellation policies
Infinite spawning Lifecycle limits

Critical design principle:

Agents may run in parallel, but durable state mutation must be controlled.

Recommended ownership:

Resource Owner
Long-term memory Memory Agent
Workflow state Workflow runtime
User-facing response Persona/Response Composer
Runtime code changes Runtime evolution pipeline

⸻

Self-Healing Workflows

The system should support self-healing execution.

Capabilities discussed:

* Failure detection
* Retry strategies
* Agent replacement
* Rollbacks
* Workflow repair
* Timeout handling
* Escalation to user

Repair Agent responsibilities:

* Replace stuck agents
* Retry with different models
* Recover workflows
* Trigger rollback procedures

⸻

Controlled Runtime Evolution

A major topic discussed was autonomous runtime improvement.

Important distinction:

The system should not rewrite itself freely in production.

Instead:

Observe issue
  ↓
Propose change
  ↓
Implement in sandbox
  ↓
Run tests
  ↓
Evaluator review
  ↓
Human approval
  ↓
Deploy safely

Allowed autonomous changes initially:

* Prompt optimization
* Workflow optimization
* Tool routing improvements
* Retry strategy improvements

Restricted areas:

* Security systems
* Permission layers
* Core orchestration runtime
* Production deployment logic

⸻

Genetic Programming Integration

The possibility of using Genetic Programming (GP) was explored.

Conclusion:

GP can provide value as an optimization engine, not as the primary intelligence layer.

Good GP targets:

Optimization Target Suitability
Prompt variants Excellent
Routing policies Excellent
Tool chains Good
Retry strategies Good
Workflow sequences Good
Runtime core mutation Dangerous

Key concept:

GP requires measurable fitness functions.

Example:

Fitness = SuccessRate
        - Cost
        - Latency
        - HallucinationRate
        - UserCorrections

The GP system should operate like:

Generate variants
  ↓
Sandbox evaluation
  ↓
Measure fitness
  ↓
Promote successful candidates
  ↓
Require approval for critical changes

⸻

Technical Stack Discussion

Recommended stack:

Layer Technology
Backend Python + FastAPI
Agent Orchestration LangGraph
Durable Workflows Temporal
Model Routing LiteLLM
Database PostgreSQL
Vector DB Qdrant
Graph DB Neo4j or Memgraph
Cache Redis
Event Bus NATS or Redis Streams
Sandbox Docker
UI React + TypeScript
Observability OpenTelemetry + Grafana

Key architecture principle:

* Temporal owns durable execution
* LangGraph owns reasoning orchestration
* LiteLLM owns model abstraction
* PostgreSQL owns durable truth
* Markdown Wiki owns canonical memory

⸻

Architectural Philosophy

The system should prioritize:

1. Governance before autonomy
2. Reliability before intelligence
3. Observability before scale
4. Memory quality before memory quantity
5. Controlled evolution before unrestricted self-modification
6. Human trust before full automation

⸻

Toy Project Repositioning

An important architectural shift occurred during the discussion.

Initially, the system was framed similarly to a production-grade adaptive runtime.

The direction later evolved into:

* A personal project
* A toy/lab environment
* A cognitive experimentation platform
* A playful orchestration system

This changed several design decisions.

Simplifications Introduced

Removed/Simplified Reason
Kubernetes Too heavy for a toy project
Temporal Overkill initially
Enterprise observability Not required
Distributed infrastructure Local-first preferred
Complex security orchestration Simpler governance acceptable
Production deployment systems Out of scope
Enterprise scalability Not a goal

Simplified Technical Stack

Layer Technology
Backend Python + FastAPI
Orchestration LangGraph
Model Routing LiteLLM
Providers OpenRouter + Ollama
Database SQLite initially
Memory Obsidian Markdown Vault
Frontend CLI + Telegram
Deployment Local machine / Raspberry Pi

New Conceptual Identity

Ubongo became positioned as:

* A personal adaptive AI companion
* A modular AI experimentation environment
* A mood-aware orchestration playground
* A configurable AI personality ecosystem

rather than:

* Enterprise software
* A SaaS platform
* A production AGI runtime

⸻

Mood-Aware and Personality-Centric Design

A major insight during the discussion was that Ubongo should adapt to:

* User tone
* Mood
* Communication style
* Emotional context
* Interaction type

Examples:

Situation Desired Behavior
Work mode Professional and concise
Architecture brainstorming Technical and deep
Casual conversation Relaxed and playful
Frustration Lower-friction responses
Private/adult mode Informal mature interactions
Research mode Analytical and detailed

The system should behave more like:

* Different aspects of the same AI mind

instead of:

* Completely disconnected assistants

This led to the concept of:

* Mood-aware routing
* Personality overlays
* Shared memory across personas
* Adaptive orchestration

⸻

Ubongo Naming and Identity

The project was renamed to:

Ubongo

The name was selected because it means:

* Brain
* Mind

in Swahili.

The name aligns closely with the project’s goals:

* Persistent cognition
* Memory
* Adaptive reasoning
* Modular intelligence
* AI personalities
* Cognitive orchestration

Ubongo was considered a better fit than:

* Generic AI assistant names
* Enterprise-oriented naming
* Overly technical platform branding

because it feels:

* More personal
* More alive
* More experimental
* More cognitive

⸻

Final Conceptual Position

The final conceptual direction became:

The Adaptive Agent Runtime is not:

* A chatbot
* A simple agent framework
* A stateless AI interface

It is:

* A persistent orchestrated AI ecosystem
* A governed cognitive runtime
* A digital organization of agents
* A multi-model adaptive operating environment
* A memory-centric AI platform

The architecture combines:

* Multi-agent systems
* Workflow orchestration
* Distributed systems concepts
* Human interaction modeling
* Knowledge management
* Governance systems
* Long-term memory
* Controlled autonomy

into a unified adaptive runtime.
