CREATE TABLE IF NOT EXISTS b04_subscription_creation_keys (
    creation_key TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL UNIQUE REFERENCES b04_subscriptions(subscription_id)
);

CREATE TABLE IF NOT EXISTS b04_public_event_suppressed (
    window_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    subscription_revision INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('sealed_membership', 'member_limit')),
    suppressed_at TEXT NOT NULL,
    PRIMARY KEY (window_id, subscription_id, subscription_revision, event_key, event_version)
);

CREATE TABLE IF NOT EXISTS b04_event_commentary (
    commentary_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('started', 'complete', 'fallback')),
    text TEXT,
    CHECK ((state='complete' AND text IS NOT NULL) OR (state!='complete' AND text IS NULL))
);

CREATE TABLE IF NOT EXISTS b04_declared_subscription_gates (
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    field TEXT NOT NULL,
    PRIMARY KEY (principal_id, module_id, field)
);

CREATE TABLE IF NOT EXISTS b04_declared_subscription_gate_registrations (
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    field TEXT NOT NULL,
    PRIMARY KEY (principal_id, module_id)
);
