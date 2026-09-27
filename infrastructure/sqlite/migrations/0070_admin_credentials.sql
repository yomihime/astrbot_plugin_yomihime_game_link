CREATE TABLE IF NOT EXISTS admin_credentials (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    state TEXT NOT NULL CHECK (state IN ('UNINITIALIZED', 'ACTIVE', 'REVOKED')),
    generation INTEGER NOT NULL CHECK (generation >= 0),
    verifier_digest BLOB,
    CHECK (
        (state = 'UNINITIALIZED' AND generation = 0 AND verifier_digest IS NULL)
        OR (state = 'ACTIVE' AND generation >= 1 AND verifier_digest IS NOT NULL
            AND typeof(verifier_digest) = 'blob' AND length(verifier_digest) = 32)
        OR (state = 'REVOKED' AND generation >= 1 AND verifier_digest IS NULL)
    )
);

INSERT INTO admin_credentials(singleton, state, generation, verifier_digest)
VALUES (1, 'UNINITIALIZED', 0, NULL);
