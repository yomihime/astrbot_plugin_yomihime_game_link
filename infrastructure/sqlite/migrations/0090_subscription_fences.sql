ALTER TABLE config_entries ADD COLUMN subscription_transition_at TEXT;

ALTER TABLE b04_collection_jobs ADD COLUMN gate_revision INTEGER;
ALTER TABLE b04_collection_jobs ADD COLUMN intent_revision INTEGER;
ALTER TABLE b04_delivery_events ADD COLUMN gate_revision INTEGER;
ALTER TABLE b04_delivery_events ADD COLUMN intent_revision INTEGER;
ALTER TABLE b04_evaluation_states ADD COLUMN gate_revision INTEGER;
ALTER TABLE b04_evaluation_states ADD COLUMN intent_revision INTEGER;

CREATE TABLE subscription_gate_bootstrap (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    phase TEXT NOT NULL CHECK (phase IN ('pending', 'complete'))
);
INSERT INTO subscription_gate_bootstrap(singleton, phase) VALUES (1, 'pending');

CREATE TABLE subscription_gate_initializations (
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    field TEXT NOT NULL,
    PRIMARY KEY (principal_id, module_id, field)
);
