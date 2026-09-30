"""SQLite connection (WAL) and ordered schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1: snapshots (frozen data), symbol catalogue, settings
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE snapshots (
      id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      symbol TEXT NOT NULL,
      provider_symbol TEXT NOT NULL DEFAULT '',
      label TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT 'ohlcv',
      fetched_at REAL NOT NULL,
      req_start TEXT NOT NULL DEFAULT '',
      req_end TEXT NOT NULL DEFAULT '',
      actual_start TEXT NOT NULL DEFAULT '',
      actual_end TEXT NOT NULL DEFAULT '',
      frequency TEXT NOT NULL DEFAULT 'D',
      periods_per_year REAL NOT NULL DEFAULT 252,
      currency TEXT NOT NULL DEFAULT '',
      adjusted INTEGER NOT NULL DEFAULT 0,
      unit TEXT NOT NULL DEFAULT '',
      rows INTEGER NOT NULL DEFAULT 0,
      sha256 TEXT NOT NULL,
      columns TEXT NOT NULL DEFAULT '[]',
      terms TEXT NOT NULL DEFAULT '',
      delay TEXT NOT NULL DEFAULT '',
      extra TEXT NOT NULL DEFAULT '{}',
      note TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX snapshots_symbol ON snapshots(symbol);
    CREATE INDEX snapshots_fetched ON snapshots(fetched_at);
    CREATE TABLE symbols (
      provider TEXT NOT NULL,
      symbol TEXT NOT NULL,
      name TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT '',
      currency TEXT NOT NULL DEFAULT '',
      unit TEXT NOT NULL DEFAULT '',
      frequency TEXT NOT NULL DEFAULT 'D',
      seed INTEGER NOT NULL DEFAULT 0,
      extra TEXT NOT NULL DEFAULT '{}',
      last_seen_ts REAL,
      PRIMARY KEY (provider, symbol)
    );
    """,
    # 2: theses, evidence, checks
    """
    CREATE TABLE theses (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      claim TEXT NOT NULL DEFAULT '',
      assets TEXT NOT NULL DEFAULT '[]',
      as_of TEXT NOT NULL,
      horizon TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'open',
      assumptions TEXT NOT NULL DEFAULT '[]',
      rival TEXT NOT NULL DEFAULT '',
      rules TEXT NOT NULL DEFAULT '[]',
      notes TEXT NOT NULL DEFAULT '',
      created_ts REAL NOT NULL,
      updated_ts REAL NOT NULL
    );
    CREATE INDEX theses_status ON theses(status);
    CREATE TABLE thesis_evidence (
      id TEXT PRIMARY KEY,
      thesis_id TEXT NOT NULL,
      side TEXT NOT NULL,
      kind TEXT NOT NULL,
      ref TEXT NOT NULL DEFAULT '',
      title TEXT NOT NULL DEFAULT '',
      quote TEXT NOT NULL DEFAULT '',
      metric TEXT NOT NULL DEFAULT '{}',
      added_ts REAL NOT NULL
    );
    CREATE INDEX evidence_thesis ON thesis_evidence(thesis_id);
    CREATE TABLE thesis_checks (
      id TEXT PRIMARY KEY,
      thesis_id TEXT NOT NULL,
      ts REAL NOT NULL,
      as_of TEXT NOT NULL,
      signature TEXT NOT NULL DEFAULT '',
      results TEXT NOT NULL DEFAULT '[]',
      tripped INTEGER NOT NULL DEFAULT 0,
      status_before TEXT NOT NULL DEFAULT '',
      status_after TEXT NOT NULL DEFAULT '',
      snapshots TEXT NOT NULL DEFAULT '[]',
      warnings TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX checks_thesis ON thesis_checks(thesis_id, ts);
    CREATE TABLE committee_runs (
      id TEXT PRIMARY KEY,
      thesis_id TEXT NOT NULL,
      created_ts REAL NOT NULL,
      mode TEXT NOT NULL DEFAULT 'material',
      result TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX committee_thesis ON committee_runs(thesis_id, created_ts);
    """,
    # 3: strategies, experiments log, portfolios
    """
    CREATE TABLE strategies (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      spec TEXT NOT NULL,
      spec_hash TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT '',
      created_ts REAL NOT NULL,
      updated_ts REAL NOT NULL
    );
    CREATE UNIQUE INDEX strategies_name ON strategies(name);
    CREATE TABLE experiments (
      id TEXT PRIMARY KEY,
      created_ts REAL NOT NULL,
      kind TEXT NOT NULL DEFAULT 'backtest',
      family TEXT NOT NULL DEFAULT '',
      strategy_id TEXT,
      name TEXT NOT NULL DEFAULT '',
      spec_hash TEXT NOT NULL DEFAULT '',
      spec TEXT NOT NULL DEFAULT '{}',
      as_of TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'ok',
      error TEXT NOT NULL DEFAULT '',
      summary TEXT NOT NULL DEFAULT '{}',
      snapshot_ids TEXT NOT NULL DEFAULT '[]',
      parent_id TEXT,
      sharpe_pp REAL,
      n_obs INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX experiments_family ON experiments(family, kind);
    CREATE INDEX experiments_created ON experiments(created_ts);
    CREATE TABLE portfolios (
      name TEXT PRIMARY KEY,
      currency TEXT NOT NULL DEFAULT 'EUR',
      holdings TEXT NOT NULL DEFAULT '[]',
      updated_ts REAL NOT NULL
    );
    """,
]


class Database:
    """One connection shared by every thread, guarded by a re-entrant lock.

    The app is the only writer; the MCP bridge never opens this file.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, sql in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                script = f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({index});\nCOMMIT;"
                try:
                    self.conn.executescript(script)
                except Exception:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise

    def version(self) -> int:
        row = self.one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"] or 0)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def transaction(self):
        """`with db.transaction():` — BEGIN IMMEDIATE / COMMIT (ROLLBACK on error) under the lock."""
        return _Transaction(self)

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.conn.execute("COMMIT")
            else:
                self.db.conn.execute("ROLLBACK")
        finally:
            self.db.lock.release()
        return False
