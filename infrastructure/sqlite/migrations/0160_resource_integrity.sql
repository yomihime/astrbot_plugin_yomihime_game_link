-- Verified content evidence is separate from ownership/expiry and never grants
-- access. File version equality permits reuse across process restarts.
CREATE TABLE resource_integrity_snapshots (
    scope_key TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    file_identity TEXT NOT NULL,
    PRIMARY KEY(scope_key,asset_id)
);
