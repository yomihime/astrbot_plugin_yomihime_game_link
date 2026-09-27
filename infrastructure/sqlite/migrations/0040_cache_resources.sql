CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'user', 'authorized')),
    user_id TEXT,
    grant_id TEXT,
    grant_revision INTEGER,
    payload_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_version INTEGER NOT NULL DEFAULT 0 CHECK (source_version >= 0),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    invalidated INTEGER NOT NULL DEFAULT 0 CHECK (invalidated IN (0, 1)),
    PRIMARY KEY (cache_key, visibility, user_id, grant_id, grant_revision),
    CHECK (
        (visibility = 'public' AND user_id IS NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (visibility = 'user' AND user_id IS NOT NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (visibility = 'authorized' AND user_id IS NOT NULL AND grant_id IS NOT NULL AND grant_revision IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS cache_expiry_idx
    ON cache_entries (expires_at, invalidated);

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('public', 'user', 'authorized')),
    user_id TEXT,
    grant_id TEXT,
    grant_revision INTEGER,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    expires_at TEXT,
    temporary INTEGER NOT NULL CHECK (temporary IN (0, 1)),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (asset_id, scope_kind, user_id, grant_id, grant_revision),
    CHECK (
        (scope_kind = 'public' AND user_id IS NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (scope_kind = 'user' AND user_id IS NOT NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (scope_kind = 'authorized' AND user_id IS NOT NULL AND grant_id IS NOT NULL AND grant_revision IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS assets_expiry_idx
    ON assets (expires_at, temporary);
