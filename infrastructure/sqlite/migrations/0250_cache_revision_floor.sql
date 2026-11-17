-- New incarnations must start above all revisions retired by physical deletion.
-- One scalar suffices because revisions within each live key already increase.
CREATE TABLE cache_revision_floor (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    retired_revision INTEGER NOT NULL CHECK (
        typeof(retired_revision) = 'integer' AND retired_revision >= 0
    )
);
INSERT INTO cache_revision_floor VALUES (1, 0);

CREATE TRIGGER cache_remember_retired_revision AFTER DELETE ON cache_entries
BEGIN
    UPDATE cache_revision_floor SET retired_revision = MIN(OLD.revision, 9223372036854775807)
    WHERE singleton = 1 AND retired_revision < OLD.revision;
END;
