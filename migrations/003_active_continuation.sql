BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS ux_commands_active_search_continue
    ON commands ((payload->>'search_id'))
    WHERE type='search_continue' AND status IN ('queued','running');

INSERT INTO schema_migrations(version) VALUES (3)
ON CONFLICT (version) DO NOTHING;

COMMIT;
