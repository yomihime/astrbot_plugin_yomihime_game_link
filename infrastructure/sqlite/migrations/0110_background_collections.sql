CREATE TABLE b04_background_jobs (
    job_key TEXT PRIMARY KEY REFERENCES b04_collection_jobs(job_key),
    module_id TEXT NOT NULL,
    config_principal_id TEXT NOT NULL,
    config_revision INTEGER NOT NULL CHECK (config_revision >= 1),
    declaration_digest TEXT NOT NULL,
    intent_revision INTEGER NOT NULL CHECK (intent_revision >= 1),
    generation INTEGER NOT NULL CHECK (generation >= 1),
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);
CREATE INDEX b04_background_jobs_module_idx ON b04_background_jobs(module_id, active);
