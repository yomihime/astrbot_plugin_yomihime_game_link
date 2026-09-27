CREATE TABLE IF NOT EXISTS config_state (
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (principal_id, module_id)
);

CREATE TABLE IF NOT EXISTS config_entries (
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT,
    secret_token TEXT,
    secret_principal_id TEXT,
    secret_module_id TEXT,
    secret_field TEXT,
    secret_operation_id TEXT,
    secret_state TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (principal_id, module_id, field),
    FOREIGN KEY (principal_id, module_id)
        REFERENCES config_state (principal_id, module_id)
        ON DELETE CASCADE,
    CHECK (
        (value_json IS NOT NULL AND secret_token IS NULL AND secret_state IS NULL)
        OR
        (value_json IS NULL AND secret_token IS NOT NULL AND secret_state = 'active')
        OR
        (value_json IS NULL AND secret_token IS NULL AND secret_state = 'tombstoned')
    ),
    CHECK (
        secret_token IS NULL
        OR (
            secret_principal_id IS NOT NULL
            AND secret_module_id IS NOT NULL
            AND secret_field IS NOT NULL
            AND secret_operation_id IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS ix_config_entries_target
    ON config_entries (principal_id, module_id, revision);

CREATE TABLE IF NOT EXISTS module_collections (
    module_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('public', 'user', 'authorized')),
    indexes_json TEXT NOT NULL,
    PRIMARY KEY (module_id, collection)
);

CREATE TABLE IF NOT EXISTS module_records (
    module_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('public', 'user', 'authorized')),
    owner_user_id TEXT NOT NULL,
    owner_grant_id TEXT NOT NULL,
    owner_grant_revision INTEGER NOT NULL,
    record_key TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    payload_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (
        module_id, collection, owner_kind, owner_user_id,
        owner_grant_id, owner_grant_revision, record_key
    ),
    FOREIGN KEY (module_id, collection)
        REFERENCES module_collections (module_id, collection)
        ON DELETE CASCADE,
    CHECK (
        (owner_kind = 'public'
            AND owner_user_id = ''
            AND owner_grant_id = ''
            AND owner_grant_revision = 0)
        OR
        (owner_kind = 'user'
            AND owner_user_id IS NOT NULL
            AND owner_grant_id = ''
            AND owner_grant_revision = 0)
        OR
        (owner_kind = 'authorized'
            AND owner_user_id IS NOT NULL
            AND owner_grant_id IS NOT NULL
            AND owner_grant_revision IS NOT NULL
            AND owner_grant_revision >= 1)
    )
);

CREATE INDEX IF NOT EXISTS ix_module_records_query
    ON module_records (module_id, collection, owner_kind, owner_user_id,
                       owner_grant_id, owner_grant_revision, record_key);
