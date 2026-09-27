CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    secret_token TEXT,
    secret_principal_id TEXT,
    secret_module_id TEXT,
    secret_field TEXT,
    secret_operation_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired', 'unavailable')),
    expires_at TEXT,
    UNIQUE (principal_id, module_id, account_id)
);

CREATE INDEX IF NOT EXISTS grants_owner_idx
    ON grants (principal_id, module_id, status);

CREATE TABLE IF NOT EXISTS login_sessions (
    session_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'cancelled', 'expired')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (principal_id, module_id, generation)
);

CREATE INDEX IF NOT EXISTS login_sessions_owner_idx
    ON login_sessions (principal_id, module_id, generation DESC);

CREATE TABLE IF NOT EXISTS secret_receipts (
    secret_token TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    field TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    expected_config_revision INTEGER NOT NULL CHECK (expected_config_revision >= 0),
    ledger_revision INTEGER NOT NULL CHECK (ledger_revision >= 1),
    state TEXT NOT NULL CHECK (state IN ('staged', 'claimed', 'active', 'cas_conflict', 'orphan', 'delete_failed', 'recoverable', 'tombstoned')),
    payload_name TEXT NOT NULL,
    metadata_revision INTEGER NOT NULL DEFAULT 0 CHECK (metadata_revision >= 0),
    UNIQUE (principal_id, module_id, field, operation_id)
);

CREATE INDEX IF NOT EXISTS secret_receipts_target_idx
    ON secret_receipts (principal_id, module_id, field, state);
