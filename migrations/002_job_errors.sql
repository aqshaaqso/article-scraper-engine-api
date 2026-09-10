BEGIN;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS error_code text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS error_detail text;

INSERT INTO schema_migrations(version) VALUES (2)
ON CONFLICT (version) DO NOTHING;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='scraper_api') THEN
        GRANT SELECT ON schema_migrations TO scraper_api;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='scraper_worker') THEN
        GRANT SELECT ON schema_migrations TO scraper_worker;
    END IF;
END $$;

COMMIT;
