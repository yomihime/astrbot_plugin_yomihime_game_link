CREATE TABLE IF NOT EXISTS ordinary_config_migrations (
    principal_id TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (principal_id, migration_id)
);
