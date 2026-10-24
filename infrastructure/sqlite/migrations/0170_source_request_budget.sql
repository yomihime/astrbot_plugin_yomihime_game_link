CREATE TABLE source_request_attempts (
    attempt_id INTEGER PRIMARY KEY,
    host TEXT NOT NULL,
    attempted_at REAL NOT NULL
);
CREATE INDEX source_request_attempts_host_time
    ON source_request_attempts(host, attempted_at);
CREATE TABLE source_request_cooldowns (
    host TEXT PRIMARY KEY,
    until_at REAL NOT NULL
);
