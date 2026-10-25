-- Only resources explicitly stored through the managed API enter this index.
-- Existing assets and pending/orphan recovery inventories are never adopted.
CREATE TABLE managed_resource_entries (
    scope_key TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    expires_at TEXT NOT NULL,
    last_access TEXT NOT NULL,
    evicting INTEGER NOT NULL DEFAULT 0 CHECK(evicting IN (0,1)),
    PRIMARY KEY(scope_key, asset_id)
);
CREATE INDEX managed_resource_expiry_idx ON managed_resource_entries(scope_key,evicting,expires_at);
CREATE INDEX managed_resource_lru_idx ON managed_resource_entries(scope_key,last_access,asset_id);
CREATE TABLE managed_resource_scopes (
    scope_key TEXT PRIMARY KEY,
    evictions INTEGER NOT NULL DEFAULT 0 CHECK(evictions >= 0)
);

