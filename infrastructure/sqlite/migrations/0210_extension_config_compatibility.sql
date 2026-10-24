CREATE TABLE extension_config_compatibility_audit (
    package_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    field TEXT NOT NULL,
    rule_digest TEXT NOT NULL,
    previous_revision INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    previous_value_json TEXT NOT NULL,
    value_json TEXT NOT NULL,
    applied_at REAL NOT NULL,
    PRIMARY KEY(package_id,principal_id,module_id,field,rule_digest)
);
