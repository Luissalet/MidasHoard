"""SQLite connection (WAL) and ordered schema migrations."""

from __future__ import annotations

import functools

from .hoard_link import sqlkit

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


# the shared SQLite layer: WAL, busy timeout, re-entrant ``tx()`` / ``transaction()``, foreign keys, ordered migrations
Database = functools.partial(sqlkit.Database, migrations=MIGRATIONS, foreign_keys=True)
