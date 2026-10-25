CREATE TABLE extension_installation_grants (
    package_id TEXT PRIMARY KEY,
    plugin_name TEXT NOT NULL,
    package_root TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    approved_policy TEXT NOT NULL,
    permission_digest TEXT NOT NULL,
    approved_manifest_digest TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at REAL NOT NULL
);
CREATE TABLE extension_installation_audit (
    sequence INTEGER PRIMARY KEY,
    package_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    permission_digest TEXT NOT NULL,
    actor TEXT NOT NULL,
    recorded_at REAL NOT NULL
);
