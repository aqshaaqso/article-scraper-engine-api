BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version bigint PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commands (
    id uuid PRIMARY KEY,
    type text NOT NULL CHECK (type IN ('scrape_one', 'scrape_batch', 'search', 'search_continue')),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    correlation_id text,
    lease_owner text,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code text,
    error_detail text,
    result_json jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_commands_claim
    ON commands (created_at) WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS search_runs (
    id uuid PRIMARY KEY,
    request_json jsonb NOT NULL,
    state_json jsonb NOT NULL,
    status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'running', 'discovery_complete', 'failed')),
    lease_owner text,
    lease_until timestamptz,
    requests_attempted integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id uuid PRIMARY KEY,
    command_id uuid REFERENCES commands(id),
    search_id uuid REFERENCES search_runs(id),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed')),
    total integer NOT NULL CHECK (total >= 0),
    completed integer NOT NULL DEFAULT 0,
    succeeded integer NOT NULL DEFAULT 0,
    failed integer NOT NULL DEFAULT 0,
    error_code text,
    error_detail text,
    search_context jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS job_items (
    id bigserial PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position integer NOT NULL,
    url text NOT NULL,
    normalized_url text,
    domain text,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'success', 'failed')),
    lease_owner text,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    error_code text,
    error_detail text,
    article_json jsonb,
    date_status text NOT NULL DEFAULT 'not_filtered',
    included boolean,
    date_basis text,
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (job_id, position)
);

CREATE INDEX IF NOT EXISTS idx_job_items_claim
    ON job_items (id) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items (job_id, position);

CREATE TABLE IF NOT EXISTS domain_rate_limits (
    domain text PRIMARY KEY,
    next_allowed_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id text PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version) VALUES (1)
ON CONFLICT (version) DO NOTHING;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='scraper_api') THEN
        GRANT SELECT ON schema_migrations TO scraper_api;
        GRANT SELECT, INSERT ON commands, jobs, job_items, search_runs TO scraper_api;
        GRANT UPDATE ON search_runs TO scraper_api;
        GRANT USAGE, SELECT ON SEQUENCE job_items_id_seq TO scraper_api;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='scraper_worker') THEN
        GRANT SELECT ON schema_migrations TO scraper_worker;
        GRANT SELECT, INSERT, UPDATE, DELETE ON commands, jobs, job_items, search_runs,
            domain_rate_limits, worker_heartbeats TO scraper_worker;
        GRANT USAGE, SELECT ON SEQUENCE job_items_id_seq TO scraper_worker;
    END IF;
END $$;

COMMIT;
