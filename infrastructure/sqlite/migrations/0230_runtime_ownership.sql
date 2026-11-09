CREATE TABLE runtime_owners (
    scope TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    core_epoch INTEGER NOT NULL CHECK(core_epoch > 0),
    package_epoch INTEGER NOT NULL CHECK(package_epoch >= 0),
    accepting INTEGER NOT NULL CHECK(accepting IN (0,1)),
    revision INTEGER NOT NULL CHECK(revision > 0),
    grant_revision INTEGER NOT NULL DEFAULT 0,
    digest TEXT NOT NULL,
    boot_id TEXT NOT NULL
);
CREATE TABLE runtime_transitions (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    from_digest TEXT,
    to_digest TEXT NOT NULL,
    from_epoch INTEGER NOT NULL,
    from_boot_id TEXT,
    boot_id TEXT NOT NULL,
    to_epoch INTEGER NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('PREPARED','OLD_FENCED','OLD_QUIET',
      'COMMITTED_CLOSED','PUBLISHED','ACTIVE','FINALIZED','WAITING_CLEANUP','FAILED')),
    resume_phase TEXT,
    error_code TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE runtime_data_contracts (
    package_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    writes_version INTEGER NOT NULL
);
