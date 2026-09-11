# Hybrid FastAPI + Go Architecture

FastAPI is the public middleware. It authenticates and validates API requests,
creates durable commands in PostgreSQL, and reads progress/results. It performs
no article or provider network access when `DATABASE_URL` is configured.

The Go worker owns URL security, robots policy, distributed domain throttling,
fetching, extraction, SerpAPI discovery, historical checkpoints, retries, and
date classification. PostgreSQL is both the durable queue and result store.

## Development stack

```powershell
Copy-Item .env.example .env
# Set SCRAPER_API_KEY and, only for search, SERPAPI_API_KEY.
docker compose up -d --build --wait
```

The compose stack starts PostgreSQL, runs the Go migration once, then starts the
FastAPI middleware on port 8010 and the Go worker. Set `API_PORT` when that host
port is already occupied, and use `docker compose up --scale go-worker=2` to
exercise multiple worker replicas. Credentials in `compose.yaml`
are development-only defaults; production must inject separate secrets.

Use `GET /health` for process health and `GET /ready` for middleware/database
readiness. Authenticated `GET /metrics` exposes Prometheus-compatible queue,
worker, lease, result, error, search, and domain-latency metrics. The worker
container health check verifies its PostgreSQL heartbeat.

## Ownership boundaries

- `migrations/`: canonical PostgreSQL schema, owned by the Go migration command.
- `src/article_scraper_lab/postgres_gateway.py`: thin middleware repository.
- `cmd/worker` and `internal/`: Go background engine.
- FastAPI has no local scraper or SQLite fallback. `DATABASE_URL` is mandatory;
  all scraping, discovery, extraction, and classification run in the Go worker.

Legacy SQLite data is not read or migrated. Do not expose the development
database passwords in production.
