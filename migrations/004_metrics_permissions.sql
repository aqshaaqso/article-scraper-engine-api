BEGIN;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='scraper_api') THEN
        GRANT SELECT ON worker_heartbeats TO scraper_api;
    END IF;
END $$;

INSERT INTO schema_migrations(version) VALUES (4)
ON CONFLICT (version) DO NOTHING;

COMMIT;
