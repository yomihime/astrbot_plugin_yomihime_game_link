CREATE TABLE extension_publisher_trust (
 key_id TEXT PRIMARY KEY, public_key BLOB NOT NULL, scope_policy TEXT NOT NULL,
 revoked INTEGER NOT NULL CHECK(revoked IN (0,1)), revision INTEGER NOT NULL,
 actor_ref TEXT NOT NULL
);
CREATE TABLE extension_package_grants (
 package_id TEXT PRIMARY KEY, publisher_key_id TEXT NOT NULL, plugin_id TEXT NOT NULL,
 permission_json TEXT NOT NULL, permission_digest TEXT NOT NULL,
 revoked INTEGER NOT NULL CHECK(revoked IN (0,1)), revision INTEGER NOT NULL,
 actor_ref TEXT NOT NULL
);
CREATE TABLE extension_artifact_approvals (
 artifact_digest TEXT PRIMARY KEY, package_id TEXT NOT NULL, plugin_id TEXT NOT NULL,
 grant_revision INTEGER NOT NULL, revoked INTEGER NOT NULL CHECK(revoked IN (0,1)),
 revision INTEGER NOT NULL, actor_ref TEXT NOT NULL
);
CREATE TABLE extension_release_heads (
 package_id TEXT NOT NULL, publisher_key_id TEXT NOT NULL, channel TEXT NOT NULL,
 release_sequence INTEGER NOT NULL, artifact_digest TEXT NOT NULL,
 PRIMARY KEY(package_id,publisher_key_id,channel)
);
