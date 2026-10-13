PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  broker TEXT NOT NULL CHECK (broker = 'alpaca'),
  broker_mode TEXT NOT NULL CHECK (broker_mode = 'paper'),
  auth_type TEXT NOT NULL CHECK (auth_type IN ('api_key', 'oauth')),
  credentials_ciphertext TEXT NOT NULL,
  credentials_iv TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  execution_enabled INTEGER NOT NULL DEFAULT 0 CHECK (execution_enabled IN (0, 1)),
  max_order_notional REAL NOT NULL DEFAULT 100,
  max_total_notional REAL NOT NULL DEFAULT 500,
  rebalance_threshold REAL NOT NULL DEFAULT 0.03,
  max_target_weight REAL NOT NULL DEFAULT 0.18,
  max_gross_target REAL NOT NULL DEFAULT 0.90,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE access_invites (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE account_snapshots (
  account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  snapshot_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE fleet_runs (
  id TEXT PRIMARY KEY,
  signal_date TEXT NOT NULL,
  signal_hash TEXT NOT NULL,
  execute_requested INTEGER NOT NULL CHECK (execute_requested IN (0, 1)),
  account_count INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE account_runs (
  id TEXT PRIMARY KEY,
  fleet_run_id TEXT NOT NULL REFERENCES fleet_runs(id) ON DELETE CASCADE,
  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  order_count INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  error_message TEXT,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (fleet_run_id, account_id)
);

CREATE INDEX idx_invites_lookup ON access_invites(token_hash, expires_at);
CREATE INDEX idx_sessions_lookup ON sessions(token_hash, expires_at);
CREATE INDEX idx_account_runs_account ON account_runs(account_id, finished_at DESC);
