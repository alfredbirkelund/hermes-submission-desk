"""SQLite persistence.

One database per Hermes profile at ``<HERMES_HOME>/plugin-data/submission-desk/data.db``
(``plugins.plugin_storage.plugin_db``), so data follows the active profile and survives
``hermes plugins update`` and ``remove``. Review history is append-only: versions, check
results, reviews and attestations are inserted, never rewritten. The only in-place updates
are bookkeeping (``verified_at`` on an unchanged observation, workspace counters) and explicit
revocation/deletion by the applicant.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from . import PLUGIN_ID

SCHEMA_VERSION = 2

_SCHEMA_V1 = """
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
"""

# Tables whose rows belong to exactly one workspace (deleted together on explicit deletion).
WORKSPACE_TABLES = (
    "codes", "sources", "source_revisions", "requirements", "requirement_versions", "fields",
    "field_versions", "documents", "observations", "mappings", "mapping_versions", "check_runs",
    "check_results", "reviews", "actions", "action_versions", "attestations", "conflicts", "events",
)

# Schema 2: why a check result is unknown (engine_unavailable, parse_error, ...), so readiness can
# suggest the right fix from stored results.
_SCHEMA_V2 = """
ALTER TABLE check_results ADD COLUMN cause TEXT;
"""

_MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1, 2: _SCHEMA_V2}

CODE_PREFIX = {
    "source": "S", "requirement": "R", "field": "F", "document": "D",
    "mapping": "M", "action": "A", "conflict": "C",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def default_connect() -> sqlite3.Connection:
    """The profile-scoped plugin database (resolves HERMES_HOME on every call)."""
    override = os.environ.get("SUBMISSION_DESK_DB")
    if override:
        return connect_path(Path(override))
    from plugins.plugin_storage import plugin_db  # documented Hermes plugin storage API

    conn = plugin_db(PLUGIN_ID)
    _tune(conn)
    return conn


def default_data_dir() -> Path:
    override = os.environ.get("SUBMISSION_DESK_DB")
    if override:
        return Path(override).parent
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_ID)


def connect_path(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _tune(conn)
    return conn


def _tune(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")


class Store:
    """Thin transactional wrapper. Open per operation; never shared across threads."""

    def __init__(self, connect: Callable[[], sqlite3.Connection] | None = None):
        self._connect = connect or default_connect
        self._migrated_for: set[str] = set()

    @contextmanager
    def tx(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            if conn.isolation_level is not None:
                conn.isolation_level = None  # explicit BEGIN/COMMIT below
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        db_key = _db_key(conn)
        if db_key in self._migrated_for:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            current = int(row[0]) if row else 0
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Submission Desk data was written by a newer version (schema {current}); "
                    f"this version understands up to {SCHEMA_VERSION}. Update the plugin; data was left untouched."
                )
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _split_sql(_MIGRATIONS[version]):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        self._migrated_for.add(db_key)


def _db_key(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row else ""
    except sqlite3.Error:
        return ""


def _split_sql(script: str) -> list[str]:
    parts: list[str] = []
    for chunk in script.split(";"):
        statement = chunk.strip()
        if statement:
            parts.append(statement)
    return parts


# --- helpers used inside a transaction ----------------------------------------------------


def allocate_code(conn: sqlite3.Connection, workspace_id: str, kind: str) -> str:
    row = conn.execute("SELECT next FROM codes WHERE workspace_id = ? AND kind = ?", (workspace_id, kind)).fetchone()
    number = int(row[0]) if row else 1
    conn.execute(
        "INSERT INTO codes(workspace_id, kind, next) VALUES(?, ?, ?) "
        "ON CONFLICT(workspace_id, kind) DO UPDATE SET next = excluded.next",
        (workspace_id, kind, number + 1),
    )
    return f"{CODE_PREFIX[kind]}{number}"


def bump_revision(conn: sqlite3.Connection, workspace_id: str, now: str) -> int:
    conn.execute("UPDATE workspaces SET revision = revision + 1, updated_at = ? WHERE id = ?", (now, workspace_id))
    row = conn.execute("SELECT revision FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return int(row[0]) if row else 0


def record_event(
    conn: sqlite3.Connection,
    *,
    workspace_id: str | None,
    actor: dict[str, Any],
    type: str,
    subject: str | None = None,
    payload: Any = None,
    idempotency_key: str | None = None,
    result: Any = None,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO events(id, workspace_id, at, actor, type, subject, payload, idempotency_key, result) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), workspace_id, now, dumps(actor), type, subject,
         dumps(payload) if payload is not None else None, idempotency_key,
         dumps(result) if result is not None else None),
    )


def idempotent_result(conn: sqlite3.Connection, key: str | None) -> Any:
    if not key:
        return None
    row = conn.execute("SELECT result FROM events WHERE idempotency_key = ?", (key,)).fetchone()
    if row is None:
        return None
    return loads(row[0], default={"ok": True, "duplicate": True})
