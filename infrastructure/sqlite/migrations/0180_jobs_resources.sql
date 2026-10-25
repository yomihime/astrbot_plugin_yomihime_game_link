-- User jobs are separate from subscription collectors and their checkpoints.
CREATE TABLE durable_jobs (
    job_id TEXT PRIMARY KEY,
    module_id TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES principals(principal_id),
    handler_id TEXT NOT NULL,
    declaration_digest TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled','interrupted')),
    revision INTEGER NOT NULL CHECK(revision > 0),
    progress_json TEXT NOT NULL DEFAULT '{}',
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    run_token TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    result_bytes INTEGER NOT NULL DEFAULT 0 CHECK(result_bytes >= 0),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt > 0)
);
CREATE INDEX durable_jobs_queue ON durable_jobs(status,created_at,job_id);
CREATE INDEX durable_jobs_owner ON durable_jobs(owner_id,module_id,status);
CREATE UNIQUE INDEX durable_jobs_active_input ON durable_jobs(owner_id,module_id,handler_id,input_digest)
    WHERE status IN ('queued','running');
CREATE TABLE durable_job_requests (
    owner_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES durable_jobs(job_id) ON DELETE CASCADE,
    PRIMARY KEY(owner_id,module_id,request_key)
);
CREATE TABLE durable_job_results (
    job_id TEXT NOT NULL REFERENCES durable_jobs(job_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    entry_json TEXT NOT NULL,
    PRIMARY KEY(job_id,sequence)
);
ALTER TABLE managed_resource_entries ADD COLUMN cache_class TEXT NOT NULL DEFAULT 'default'
    CHECK(cache_class IN ('default','raw','derived','output'));
CREATE TABLE managed_resource_pins (
    pin_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY(pin_id,scope_key,asset_id)
);
CREATE INDEX managed_resource_pin_expiry ON managed_resource_pins(scope_key,asset_id,expires_at);
