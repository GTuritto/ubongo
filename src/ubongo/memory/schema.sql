CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  active_persona TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  persona TEXT, agent TEXT, skill TEXT, model TEXT,
  tokens_in INTEGER, tokens_out INTEGER
);

CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  covers_from_message_id INTEGER NOT NULL,
  covers_to_message_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  strategy TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL
);

-- sessions.auto_mode is a Phase-4 addition not in the original v0.1 schema spec;
-- persists the REPL /auto state across restarts per user preference.
CREATE TABLE IF NOT EXISTS sessions (
  user_id INTEGER PRIMARY KEY,
  last_message_at TIMESTAMP,
  active_persona TEXT,
  override_until TIMESTAMP,
  current_conversation_id INTEGER REFERENCES conversations(id),
  auto_mode INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  subject TEXT, predicate TEXT, object TEXT,
  source_message_id INTEGER REFERENCES messages(id),
  importance INTEGER DEFAULT 0,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  classification JSON NOT NULL,
  workflow JSON NOT NULL,
  execution_mode TEXT NOT NULL,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  outcome TEXT NOT NULL CHECK (outcome IN ('in_progress', 'success', 'failure', 'repaired'))
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY,
  workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
  agent TEXT NOT NULL,
  model TEXT,
  input JSON, output JSON,
  confidence REAL,
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
  outcome TEXT NOT NULL,
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  retried INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS governance_decisions (
  id INTEGER PRIMARY KEY,
  workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
  intent TEXT, risk TEXT, confidence REAL, reversibility TEXT,
  action TEXT NOT NULL,
  approval_response TEXT,
  decided_at TIMESTAMP NOT NULL
);

-- v0.5 phase 05: the grant registry. Persistent consent for a capability class;
-- the first connector turn touching a class with no active grant asks once, and
-- approving writes a row here so later turns auto-proceed. Revoking re-arms the ask.
CREATE TABLE IF NOT EXISTS grants (
  id INTEGER PRIMARY KEY,
  capability_class TEXT NOT NULL,                -- e.g. "connector:compendium"
  consequence_class TEXT NOT NULL CHECK (consequence_class IN ('reversible', 'irreversible')),
  scope TEXT NOT NULL DEFAULT '*',               -- agent name, or '*' for any agent
  purpose TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  created_at TIMESTAMP NOT NULL,
  revoked_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_grants_class_status ON grants(capability_class, status);

-- v0.5 phase 03: the resumable approval record. One row per require_approval
-- turn; the single source of truth for resuming a gated turn in any channel.
CREATE TABLE IF NOT EXISTS pending_approvals (
  decision_id INTEGER PRIMARY KEY REFERENCES governance_decisions(id),
  message TEXT NOT NULL,
  persona TEXT NOT NULL,
  auto_mode INTEGER NOT NULL DEFAULT 0,
  summary TEXT NOT NULL,
  why TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'declined')),
  created_at TIMESTAMP NOT NULL,
  resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals(status);

CREATE TABLE IF NOT EXISTS repair_runs (
  id INTEGER PRIMARY KEY,
  workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
  agent TEXT NOT NULL,                           -- the agent that failed
  failure_kind TEXT NOT NULL,                    -- FailureKind value
  original_error TEXT,                           -- AgentResult.error string
  strategy_attempted TEXT NOT NULL,              -- Strategy value
  peer_agent TEXT,                               -- when strategy=replace_with_peer
  override_model TEXT,                           -- when strategy=*_model_*
  attempt_index INTEGER NOT NULL,                -- 0-based per failed agent
  outcome TEXT NOT NULL CHECK (outcome IN ('recovered', 'failed', 'aborted')),
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_repair_runs_workflow ON repair_runs(workflow_run_id);

CREATE TABLE IF NOT EXISTS evolution_lineage (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL,
  parent_id INTEGER REFERENCES evolution_lineage(id),
  generation INTEGER NOT NULL,
  variant_text TEXT NOT NULL,
  variant_metadata JSON,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_evaluations (
  id INTEGER PRIMARY KEY,
  lineage_id INTEGER NOT NULL REFERENCES evolution_lineage(id),
  sample_set TEXT NOT NULL,
  success_rate REAL, cost REAL, latency_ms REAL,
  hallucination_rate REAL, user_correction_rate REAL,
  fitness REAL NOT NULL,
  evaluated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_promotions (
  id INTEGER PRIMARY KEY,
  lineage_id INTEGER NOT NULL REFERENCES evolution_lineage(id),
  target TEXT NOT NULL,
  proposed_at TIMESTAMP NOT NULL,
  decided_at TIMESTAMP,
  decision TEXT CHECK (decision IN ('approved', 'rejected'))
);

CREATE TABLE IF NOT EXISTS active_evolutions (
  target TEXT PRIMARY KEY,
  lineage_id INTEGER NOT NULL REFERENCES evolution_lineage(id),
  promoted_at TIMESTAMP NOT NULL
);

-- Phase 18: one row per autonomous GP cycle. Doubles as the rolling-hour
-- throttle window (sum calls_spent over ended_at in the trailing hour) and the
-- crash-recovery / round-robin log.
CREATE TABLE IF NOT EXISTS evolution_runs (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL,
  generation INTEGER NOT NULL,
  calls_spent INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL CHECK (outcome IN ('started', 'completed', 'partial', 'aborted')),
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP
);

-- Phase 18: single-row control state for the autonomous loop. Persisted so the
-- loop comes back paused after a restart and /evolution survives sessions.
CREATE TABLE IF NOT EXISTS evolution_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'off')),
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_queue (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  urgency TEXT NOT NULL CHECK (urgency IN ('low', 'normal', 'urgent')),
  source TEXT,
  created_at TIMESTAMP NOT NULL,
  deliver_after TIMESTAMP,
  delivered_at TIMESTAMP,
  expires_at TIMESTAMP,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS vault_links (
  source_path TEXT NOT NULL,
  target_path TEXT NOT NULL,
  link_type TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (source_path, target_path, link_type)
);

-- Phase 20: idempotency sidecar for message embeddings. The vec0 vectors live
-- in vec_messages (created lazily by memory/embeddings.py when sqlite-vec is
-- available); this plain table records the text hash so re-indexing unchanged
-- text makes no embedding call.
CREATE TABLE IF NOT EXISTS embedding_meta (
  message_id INTEGER PRIMARY KEY,
  text_hash TEXT NOT NULL,
  embedded_at TIMESTAMP NOT NULL
);

-- Phase 21: content hash of what the SYSTEM last wrote to each vault note, so
-- the watcher tells its own writes (hash matches) from external user edits
-- (hash differs) — no echo loop.
CREATE TABLE IF NOT EXISTS vault_state (
  path TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  last_written_at TIMESTAMP NOT NULL
);

-- Phase 21: edit/write collisions queued for the user to resolve via
-- /conflicts (a background watcher cannot prompt mid-turn).
CREATE TABLE IF NOT EXISTS vault_conflicts (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  detected_at TIMESTAMP NOT NULL,
  system_hash TEXT,
  disk_hash TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_vault_conflicts_open ON vault_conflicts(status) WHERE status = 'open';

-- Self-authored skills (the self-extension experiment). One row per drafted
-- skill candidate. A candidate is written to config/skills_candidates/<name>/
-- (quarantine, NOT scanned by skills.py) and recorded here as 'draft'. The
-- approval gate (Phase 3) flips it to 'approved' (materialized into
-- config/skills/) or 'rejected'; rollback marks 'rolled_back'. `backup_path`
-- records where a prior version was copied before an approve overwrote it, so
-- rollback can restore it. `quality` is the Phase 2 evaluation score.
CREATE TABLE IF NOT EXISTS authored_skills (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'rejected', 'rolled_back')),
  generation INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual',
  candidate JSON NOT NULL,
  quarantine_path TEXT,
  backup_path TEXT,
  quality REAL,
  created_at TIMESTAMP NOT NULL,
  decided_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_authored_skills_status ON authored_skills(status);
CREATE INDEX IF NOT EXISTS idx_authored_skills_name ON authored_skills(name, generation);

-- The autonomous authoring daemon (Phase 4). One row per cycle: the gap it
-- worked, the candidate it drafted (NULL if none), the calls it spent (the
-- rolling-hour throttle window, mirroring evolution_runs), and its outcome.
CREATE TABLE IF NOT EXISTS authoring_runs (
  id INTEGER PRIMARY KEY,
  gap TEXT,
  candidate_id INTEGER REFERENCES authored_skills(id),
  calls_spent INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL CHECK (outcome IN ('started', 'drafted', 'evaluated', 'reevaluated', 'aborted')),
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_authoring_runs_ended ON authoring_runs(ended_at);

-- Single-row control state for the authoring daemon, persisted so it comes back
-- paused after a restart (it never auto-drafts until /authoring resume).
CREATE TABLE IF NOT EXISTS authoring_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'off')),
  updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_summaries_conversation ON summaries(conversation_id);
CREATE INDEX IF NOT EXISTS idx_queue_undelivered ON notification_queue(delivered_at) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_conv ON workflow_runs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow ON agent_runs(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_lineage_target_gen ON evolution_lineage(target, generation);
CREATE INDEX IF NOT EXISTS idx_pending_undecided ON pending_promotions(decided_at) WHERE decided_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_evolution_runs_ended ON evolution_runs(ended_at);
CREATE INDEX IF NOT EXISTS idx_evolution_runs_target ON evolution_runs(target, generation);
