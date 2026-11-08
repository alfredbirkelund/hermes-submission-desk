-- Submission Desk ledger, schema version 1, frozen as released (do not edit).
-- Tests build an old ledger from it to prove that upgrades keep working.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  material_root TEXT NOT NULL,
  timezone TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  last_check_at TEXT,
  last_check_run TEXT
);

CREATE TABLE IF NOT EXISTS codes (
  workspace_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  next INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, kind)
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  label TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('file', 'text')),
  rel_path TEXT,
  origin_note TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS source_revisions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  sha256 TEXT,
  byte_size INTEGER,
  captured_at TEXT NOT NULL,
  status TEXT NOT NULL,
  method TEXT,
  detail TEXT,
  text TEXT,
  pages TEXT,
  UNIQUE (source_id, revision)
);

CREATE TABLE IF NOT EXISTS requirements (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS requirement_versions (
  id TEXT PRIMARY KEY,
  requirement_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  statement TEXT NOT NULL,
  kind TEXT NOT NULL,
  rules TEXT NOT NULL DEFAULT '{}',
  source_revision_id TEXT,
  excerpt TEXT,
  locator TEXT,
  origin TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected', 'withdrawn')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT,
  decision_note TEXT,
  UNIQUE (requirement_id, version)
);

CREATE TABLE IF NOT EXISTS fields (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS field_versions (
  id TEXT PRIMARY KEY,
  field_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  label TEXT NOT NULL,
  kind TEXT NOT NULL,
  source_revision_id TEXT,
  excerpt TEXT,
  locator TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (field_id, version)
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  label TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  removed_at TEXT,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  status TEXT NOT NULL,
  sha256 TEXT,
  byte_size INTEGER,
  mtime_ns INTEGER,
  inode INTEGER,
  device INTEGER,
  detail TEXT,
  UNIQUE (document_id, seq)
);

CREATE TABLE IF NOT EXISTS mappings (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS mapping_versions (
  id TEXT PRIMARY KEY,
  mapping_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  field_id TEXT,
  document_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
  note TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (mapping_id, version)
);

CREATE TABLE IF NOT EXISTS check_runs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  actor TEXT NOT NULL,
  engine TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT
);

CREATE TABLE IF NOT EXISTS check_results (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  workspace_id TEXT NOT NULL,
  mapping_version_id TEXT NOT NULL,
  requirement_version_id TEXT NOT NULL,
  field_version_id TEXT,
  document_id TEXT,
  sha256 TEXT,
  engine TEXT NOT NULL,
  check_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'unknown', 'not_applicable')),
  measurement TEXT,
  limit_value TEXT,
  units TEXT,
  method TEXT,
  detail TEXT,
  transient INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS check_results_key
  ON check_results (mapping_version_id, requirement_version_id, sha256, engine);

CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  mapping_id TEXT NOT NULL,
  mapping_version_id TEXT NOT NULL,
  requirement_version_id TEXT NOT NULL,
  field_version_id TEXT,
  document_id TEXT,
  sha256 TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
  note TEXT,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS action_versions (
  id TEXT PRIMARY KEY,
  action_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  description TEXT NOT NULL,
  requirement_id TEXT,
  document_id TEXT,
  origin TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected', 'withdrawn')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT,
  decision_note TEXT,
  UNIQUE (action_id, version)
);

CREATE TABLE IF NOT EXISTS attestations (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('action', 'check', 'requirement')),
  subject_id TEXT NOT NULL,
  subject_version_id TEXT NOT NULL,
  mapping_version_id TEXT,
  requirement_version_id TEXT,
  document_id TEXT,
  sha256 TEXT,
  check_name TEXT,
  statement TEXT NOT NULL,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT
);

CREATE TABLE IF NOT EXISTS conflicts (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  code TEXT NOT NULL,
  description TEXT NOT NULL,
  requirement_ids TEXT NOT NULL DEFAULT '[]',
  excerpts TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  resolution TEXT,
  governing_requirement_id TEXT,
  resolved_at TEXT,
  resolved_by TEXT,
  UNIQUE (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  type TEXT NOT NULL,
  subject TEXT,
  payload TEXT,
  idempotency_key TEXT UNIQUE,
  result TEXT
);
CREATE INDEX IF NOT EXISTS events_ws ON events (workspace_id, at);
