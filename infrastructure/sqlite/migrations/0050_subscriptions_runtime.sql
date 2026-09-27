CREATE TABLE IF NOT EXISTS b04_collection_jobs (
    job_key TEXT PRIMARY KEY,
    key_json TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('public', 'user', 'authorized')),
    owner_id TEXT,
    grant_id TEXT,
    grant_revision INTEGER,
    due_at TEXT,
    cadence_seconds REAL,
    config_revision INTEGER,
    module_epoch INTEGER,
    registry_revision INTEGER,
    lease_token TEXT,
    lease_expires_at TEXT,
    observation_id TEXT,
    CHECK ((scope_kind = 'public' AND owner_id IS NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (scope_kind = 'user' AND owner_id IS NOT NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (scope_kind = 'authorized' AND owner_id IS NOT NULL AND grant_id IS NOT NULL AND grant_revision IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS b04_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    owner_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('public', 'user', 'authorized')),
    scope_user TEXT,
    grant_id TEXT,
    grant_revision INTEGER,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    record_json TEXT NOT NULL,
    CHECK ((scope_kind = 'public' AND scope_user IS NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (scope_kind = 'user' AND scope_user IS NOT NULL AND grant_id IS NULL AND grant_revision IS NULL)
        OR (scope_kind = 'authorized' AND scope_user IS NOT NULL AND grant_id IS NOT NULL AND grant_revision IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS b04_subscriptions_owner_idx ON b04_subscriptions(owner_id, status, subscription_id);

CREATE TABLE IF NOT EXISTS b04_subscription_jobs (
    subscription_id TEXT PRIMARY KEY REFERENCES b04_subscriptions(subscription_id) ON DELETE CASCADE,
    job_key TEXT NOT NULL REFERENCES b04_collection_jobs(job_key),
    subscription_revision INTEGER NOT NULL CHECK (subscription_revision >= 1),
    association_revision INTEGER NOT NULL CHECK (association_revision >= 1),
    cadence_seconds REAL NOT NULL CHECK (cadence_seconds > 0),
    config_revision INTEGER NOT NULL CHECK (config_revision >= 1)
);
CREATE INDEX IF NOT EXISTS b04_subscription_jobs_job_idx ON b04_subscription_jobs(job_key, subscription_id);

CREATE TABLE IF NOT EXISTS b04_observations (
    observation_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL REFERENCES b04_collection_jobs(job_key),
    data_version INTEGER NOT NULL CHECK (data_version >= 1),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial', 'failed')),
    payload_json TEXT NOT NULL,
    observation_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS b04_evaluation_states (
    subscription_id TEXT PRIMARY KEY REFERENCES b04_subscriptions(subscription_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state_json TEXT NOT NULL,
    cursor_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS b04_delivery_events (
    event_key TEXT NOT NULL,
    event_version INTEGER NOT NULL CHECK (event_version >= 1),
    subscription_id TEXT NOT NULL,
    subscription_revision INTEGER NOT NULL CHECK (subscription_revision >= 1),
    owner_id TEXT NOT NULL,
    grant_id TEXT,
    grant_revision INTEGER,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 0 CHECK (attempt_number >= 0),
    retry_at TEXT,
    event_json TEXT NOT NULL,
    PRIMARY KEY (event_key, event_version, subscription_id, subscription_revision)
);
CREATE INDEX IF NOT EXISTS b04_delivery_events_owner_idx ON b04_delivery_events(owner_id, event_key, event_version);
CREATE INDEX IF NOT EXISTS b04_delivery_events_due_idx ON b04_delivery_events(state, retry_at, event_key, event_version, subscription_id, subscription_revision);

CREATE TABLE IF NOT EXISTS b04_delivery_attempts (
    event_key TEXT NOT NULL,
    event_version INTEGER NOT NULL CHECK (event_version >= 1),
    subscription_id TEXT NOT NULL,
    subscription_revision INTEGER NOT NULL CHECK (subscription_revision >= 1),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    state TEXT NOT NULL CHECK (state IN ('sending', 'sent', 'failed', 'unknown', 'cancelled')),
    attempt_json TEXT NOT NULL,
    PRIMARY KEY (event_key, event_version, subscription_id, subscription_revision, attempt_number),
    FOREIGN KEY (event_key, event_version, subscription_id, subscription_revision)
        REFERENCES b04_delivery_events(event_key, event_version, subscription_id, subscription_revision)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS b04_digest_windows (
    window_id TEXT PRIMARY KEY,
    timezone_name TEXT NOT NULL,
    local_schedule_key TEXT NOT NULL,
    utc_start TEXT NOT NULL,
    utc_end TEXT NOT NULL,
    due_at TEXT NOT NULL,
    fold_policy TEXT NOT NULL,
    gap_policy TEXT NOT NULL,
    policy_revision INTEGER NOT NULL DEFAULT 1 CHECK (policy_revision >= 1),
    window_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS b04_digest_members (
    window_id TEXT NOT NULL REFERENCES b04_digest_windows(window_id) ON DELETE CASCADE,
    recipient_key TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    subscription_revision INTEGER NOT NULL CHECK (subscription_revision >= 1),
    event_key TEXT NOT NULL,
    event_version INTEGER NOT NULL CHECK (event_version >= 1),
    association_json TEXT NOT NULL,
    PRIMARY KEY (window_id, recipient_key, subscription_id, subscription_revision, event_key, event_version)
);
CREATE INDEX IF NOT EXISTS b04_digest_members_route_idx ON b04_digest_members(window_id, recipient_key);

CREATE TABLE IF NOT EXISTS b04_digest_window_members (
    window_id TEXT NOT NULL REFERENCES b04_digest_windows(window_id) ON DELETE CASCADE,
    member_json TEXT NOT NULL,
    PRIMARY KEY (window_id, member_json)
);

CREATE TABLE IF NOT EXISTS b04_digest_envelopes (
    envelope_id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL REFERENCES b04_digest_windows(window_id),
    recipient_key TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    claim_token TEXT,
    claimed_at TEXT,
    claim_expires_at TEXT,
    retry_at TEXT,
    envelope_json TEXT NOT NULL,
    UNIQUE (window_id, recipient_key),
    CHECK ((state IN ('claimed', 'sending') AND claim_token IS NOT NULL AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL)
        OR (state NOT IN ('claimed', 'sending') AND claim_token IS NULL AND claimed_at IS NULL AND claim_expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS b04_digest_envelopes_due_idx ON b04_digest_envelopes(state, window_id, recipient_key);
CREATE INDEX IF NOT EXISTS b04_digest_envelopes_retry_idx ON b04_digest_envelopes(state, retry_at, envelope_id);

CREATE TABLE IF NOT EXISTS b04_digest_member_receipts (
    envelope_id TEXT NOT NULL REFERENCES b04_digest_envelopes(envelope_id) ON DELETE CASCADE,
    subscription_id TEXT NOT NULL,
    subscription_revision INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    disposition TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY (envelope_id, subscription_id, subscription_revision, event_key, event_version)
);
