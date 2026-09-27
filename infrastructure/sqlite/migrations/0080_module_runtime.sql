CREATE TABLE IF NOT EXISTS module_runtime_intents (
    package_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    desired_enabled INTEGER NOT NULL CHECK (desired_enabled IN (0, 1)),
    intent_revision INTEGER NOT NULL CHECK (intent_revision >= 1),
    operation_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (package_id, module_id)
);

CREATE TABLE IF NOT EXISTS module_runtime_journal (
    operation_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('enable', 'disable')),
    phase TEXT NOT NULL CHECK (
        phase IN ('prepared', 'committed', 'applied', 'compensated', 'recovery_required')
    ),
    old_enabled INTEGER NOT NULL CHECK (old_enabled IN (0, 1)),
    new_enabled INTEGER NOT NULL CHECK (new_enabled IN (0, 1)),
    old_intent_revision INTEGER NOT NULL CHECK (old_intent_revision >= 0),
    expected_intent_revision INTEGER NOT NULL CHECK (expected_intent_revision >= 0),
    expected_registry_revision INTEGER NOT NULL CHECK (expected_registry_revision >= 0),
    admin_generation INTEGER CHECK (admin_generation IS NULL OR admin_generation >= 1),
    failure_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((kind = 'enable' AND new_enabled = 1) OR (kind = 'disable' AND new_enabled = 0)),
    CHECK (failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 64)
);

CREATE INDEX IF NOT EXISTS module_runtime_journal_phase_idx
    ON module_runtime_journal(phase, updated_at, operation_id);

CREATE INDEX IF NOT EXISTS module_runtime_journal_module_idx
    ON module_runtime_journal(package_id, module_id, created_at, operation_id);
