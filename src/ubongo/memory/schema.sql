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

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_summaries_conversation ON summaries(conversation_id);
CREATE INDEX IF NOT EXISTS idx_queue_undelivered ON notification_queue(delivered_at) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_conv ON workflow_runs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow ON agent_runs(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_lineage_target_gen ON evolution_lineage(target, generation);
CREATE INDEX IF NOT EXISTS idx_pending_undecided ON pending_promotions(decided_at) WHERE decided_at IS NULL;
