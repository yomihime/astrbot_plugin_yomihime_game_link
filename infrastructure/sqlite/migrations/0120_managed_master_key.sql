CREATE TABLE managed_master_key (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    source TEXT NOT NULL DEFAULT 'unselected'
        CHECK (source IN ('unselected', 'managed', 'environment')),
    key_id TEXT,
    key_check BLOB,
    CHECK ((source = 'unselected' AND key_id IS NULL AND key_check IS NULL)
        OR (source = 'managed' AND key_id IS NOT NULL AND key_check IS NOT NULL)
        OR (source = 'environment' AND key_id IS NULL AND key_check IS NOT NULL))
);
INSERT INTO managed_master_key(singleton) VALUES(1);

CREATE TABLE managed_master_key_operations (
    operation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('initialize', 'restore', 'reset')),
    state TEXT NOT NULL CHECK (state IN ('preparing', 'committed', 'abandoned')),
    candidate_key_id TEXT,
    expected_generation INTEGER NOT NULL CHECK (expected_generation >= 0),
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
    inventory_digest TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX managed_master_key_pending
    ON managed_master_key_operations(state) WHERE state = 'preparing';

CREATE TABLE managed_master_key_retired (
    key_id TEXT PRIMARY KEY,
    retired_generation INTEGER NOT NULL CHECK (retired_generation >= 1),
    reset_operation_id TEXT NOT NULL
        REFERENCES managed_master_key_operations(operation_id)
);

ALTER TABLE secret_receipts
    ADD COLUMN key_generation INTEGER NOT NULL DEFAULT 0 CHECK (key_generation >= 0);
