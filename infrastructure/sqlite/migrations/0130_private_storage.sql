-- An interrupted operation must retain its purpose independently of selector state.
ALTER TABLE managed_master_key_operations ADD COLUMN purpose TEXT NOT NULL
    DEFAULT 'legacy' CHECK (purpose IN ('legacy', 'environment_adoption', 'storage_migration'));

CREATE TABLE private_storage_epoch (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL DEFAULT 1 CHECK (epoch >= 1),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'fenced', 'activated')),
    operation_id TEXT,
    source_identity TEXT,
    target_identity TEXT,
    inventory_digest TEXT,
    CHECK ((state = 'active' AND operation_id IS NULL)
        OR (state <> 'active' AND operation_id IS NOT NULL))
);
INSERT INTO private_storage_epoch(singleton) VALUES (1);
