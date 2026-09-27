CREATE TABLE IF NOT EXISTS b04_root_outputs (
    root_invocation_id TEXT NOT NULL,
    output_identity TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL CHECK (length(payload_fingerprint) = 64),
    state TEXT NOT NULL CHECK (state IN ('claimed', 'sending', 'completed', 'unknown')),
    output_outcome TEXT CHECK (output_outcome IN ('message', 'tool_returned', 'subscription_enqueued', 'controlled_result')),
    owner_token TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    receipt_status TEXT CHECK (receipt_status IN ('accepted', 'failed', 'unknown')),
    platform_message_id TEXT,
    error_code TEXT,
    updated_at TEXT NOT NULL,
    claim_generation INTEGER NOT NULL CHECK (claim_generation >= 1),
    PRIMARY KEY (root_invocation_id),
    CHECK ((state = 'completed' AND output_outcome IS NOT NULL
            AND ((output_outcome = 'message' AND receipt_status IS NOT NULL)
                OR (output_outcome != 'message' AND receipt_status IS NULL))
            AND ((output_outcome = 'controlled_result' AND error_code IS NOT NULL)
                OR (output_outcome != 'controlled_result' AND error_code IS NULL)))
        OR (state = 'unknown' AND output_outcome IS NULL AND receipt_status IS NULL
            AND error_code = 'interrupted_send')
        OR (state IN ('claimed', 'sending') AND output_outcome IS NULL
            AND receipt_status IS NULL AND error_code IS NULL))
);
CREATE INDEX IF NOT EXISTS b04_root_outputs_retention_idx
    ON b04_root_outputs(state, updated_at);

CREATE TABLE IF NOT EXISTS b04_root_output_generation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    next_generation INTEGER NOT NULL CHECK (next_generation >= 1)
);
INSERT OR IGNORE INTO b04_root_output_generation(singleton, next_generation)
    VALUES (1, 1);
