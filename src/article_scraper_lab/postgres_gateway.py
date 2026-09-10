"""Thin PostgreSQL command gateway used by the FastAPI middleware."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from .article_dates import apply_date_filter
from .errors import ExtractionError, FetchError, JobNotFoundError, RobotsDeniedError, UnsafeUrlError
from .models import (
    ArticleResponse,
    DateFilter,
    DateReport,
    JobAccepted,
    JobItemResponse,
    JobResponse,
    SearchAccepted,
    SearchProgress,
    SearchRequest,
)
from .request_context import request_id_var

SCHEMA_VERSION = 4


class PostgresGateway:
    def __init__(self, database_url: str, worker_count: int, sync_timeout: float) -> None:
        self._database_url = database_url
        self._worker_count = worker_count
        self._sync_timeout = sync_timeout

    @contextmanager
    def connect(self):
        with psycopg.connect(self._database_url, row_factory=dict_row) as db:
            yield db

    def start(self) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(version),0) version FROM schema_migrations"
            ).fetchone()
            if row is None or row["version"] != SCHEMA_VERSION:
                raise RuntimeError("Versi schema PostgreSQL tidak kompatibel; jalankan migrator Go")

    def shutdown(self) -> None:
        return None

    def health(self) -> bool:
        try:
            with self.connect() as db:
                return db.execute("SELECT 1").fetchone() is not None
        except psycopg.Error:
            return False

    def metrics(self) -> str:
        with self.connect() as db:
            summary = db.execute(
                """SELECT
                count(*) FILTER (WHERE status='queued') AS queue_depth,
                COALESCE(EXTRACT(EPOCH FROM now()-min(created_at)
                    FILTER (WHERE status='queued')),0) AS oldest_seconds,
                count(*) FILTER (WHERE status='running' AND lease_until<now()) AS expired
                FROM commands"""
            ).fetchone()
            workers = db.execute(
                """SELECT count(*) AS active FROM worker_heartbeats
                WHERE heartbeat_at>now()-interval '30 seconds'"""
            ).fetchone()
            items = db.execute(
                """SELECT count(*) FILTER (WHERE status='success') AS succeeded,
                count(*) FILTER (WHERE status='failed') AS failed,
                count(*) FILTER (WHERE status='running' AND lease_until<now()) AS expired
                FROM job_items"""
            ).fetchone()
            errors = db.execute(
                """SELECT error_code,count(*) AS total FROM job_items
                WHERE error_code IS NOT NULL GROUP BY error_code ORDER BY error_code"""
            ).fetchall()
            domains = db.execute(
                """SELECT domain,avg(EXTRACT(EPOCH FROM finished_at-started_at)) AS seconds
                FROM job_items WHERE domain IS NOT NULL AND finished_at IS NOT NULL
                GROUP BY domain ORDER BY domain"""
            ).fetchall()
            searches = db.execute(
                """SELECT COALESCE(sum(attempts),0) AS requests,
                count(*) FILTER (WHERE status='failed') AS failed
                FROM commands WHERE type IN ('search','search_continue')"""
            ).fetchone()
        lines = [
            "# TYPE article_scraper_command_queue_depth gauge",
            f'article_scraper_command_queue_depth {summary["queue_depth"]}',
            "# TYPE article_scraper_oldest_command_seconds gauge",
            f'article_scraper_oldest_command_seconds {float(summary["oldest_seconds"]):.3f}',
            f'article_scraper_active_workers {workers["active"]}',
            f'article_scraper_expired_leases {summary["expired"] + items["expired"]}',
            f'article_scraper_items_total{{status="success"}} {items["succeeded"]}',
            f'article_scraper_items_total{{status="failed"}} {items["failed"]}',
            f'article_scraper_search_requests_total {searches["requests"]}',
            f'article_scraper_search_failures_total {searches["failed"]}',
        ]
        lines.extend(
            f'article_scraper_errors_total{{code="{row["error_code"]}"}} {row["total"]}'
            for row in errors
        )
        lines.extend(
            "article_scraper_domain_latency_seconds"
            f'{{domain="{row["domain"]}"}} {float(row["seconds"]):.6f}'
            for row in domains
        )
        return "\n".join(lines) + "\n"

    def submit(self, urls: list[str]) -> JobAccepted:
        job_id, command_id = uuid4(), uuid4()
        payload = {"job_id": job_id.hex, "urls": urls}
        with self.connect() as db:
            db.execute(
                """INSERT INTO commands(id,type,payload,correlation_id)
                VALUES(%s,'scrape_batch',%s,%s)""",
                (command_id, json.dumps(payload), request_id_var.get()),
            )
            db.execute(
                "INSERT INTO jobs(id,command_id,total) VALUES(%s,%s,%s)",
                (job_id, command_id, len(urls)),
            )
            with db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO job_items(job_id,position,url) VALUES(%s,%s,%s)",
                    [(job_id, position, url) for position, url in enumerate(urls)],
                )
        return JobAccepted(
            job_id=job_id.hex, status="queued", total=len(urls), worker_count=self._worker_count
        )

    def scrape(self, url: str) -> ArticleResponse:
        command_id = uuid4()
        with self.connect() as db:
            db.execute(
                """INSERT INTO commands(id,type,payload,correlation_id)
                VALUES(%s,'scrape_one',%s,%s)""",
                (command_id, json.dumps({"url": url}), request_id_var.get()),
            )
        deadline = time.monotonic() + self._sync_timeout
        while time.monotonic() < deadline:
            with self.connect() as db:
                row = db.execute(
                    "SELECT status,result_json,error_code,error_detail FROM commands WHERE id=%s",
                    (command_id,),
                ).fetchone()
            if row and row["status"] == "completed":
                return ArticleResponse.model_validate(row["result_json"])
            if row and row["status"] == "failed":
                self._raise_worker_error(row["error_code"], row["error_detail"])
            time.sleep(0.1)
        raise FetchError(f"Worker belum menyelesaikan command {command_id} sebelum timeout")

    def get(self, job_id: str) -> JobResponse:
        with self.connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
            if job is None:
                raise JobNotFoundError("Job tidak ditemukan")
            rows = db.execute(
                "SELECT * FROM job_items WHERE job_id=%s ORDER BY position", (job_id,)
            ).fetchall()
        items = [
            JobItemResponse(
                position=row["position"],
                url=row["url"],
                status=row["status"],
                error_code=row["error_code"],
                error_detail=row["error_detail"],
                article=ArticleResponse.model_validate(row["article_json"])
                if row["article_json"]
                else None,
                date_status=row["date_status"],
                included=row["included"],
                date_basis=row["date_basis"],
            )
            for row in rows
        ]
        context = job["search_context"] or {}
        criteria = (
            DateFilter.model_validate(context["date_filter"])
            if context.get("date_filter")
            else None
        )
        report = apply_date_filter(items, criteria) if criteria else None
        return JobResponse(
            job_id=self._public_id(job["id"]),
            status=job["status"],
            total=job["total"],
            completed=job["completed"],
            succeeded=job["succeeded"],
            failed=job["failed"],
            error_code=job["error_code"],
            error_detail=job["error_detail"],
            created_at=job["created_at"],
            started_at=job["started_at"],
            finished_at=job["finished_at"],
            duration_ms=self._duration(job["started_at"], job["finished_at"]),
            items=items,
            date_filter=criteria,
            date_report=report,
            search_id=self._public_id(job["search_id"]) if job["search_id"] else None,
        )

    def recent(self) -> list[JobResponse]:
        with self.connect() as db:
            ids = [
                self._public_id(row["id"])
                for row in db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 20")
            ]
        return [self.get(job_id) for job_id in ids]

    def submit_search(self, body: SearchRequest) -> SearchAccepted:
        search_id, command_id = uuid4(), uuid4()
        payload = body.model_dump(mode="json")
        payload["command_id"] = command_id.hex
        if body.start_date is None:
            job_id = uuid4()
            payload["job_id"] = job_id.hex
            with self.connect() as db:
                db.execute(
                    """INSERT INTO commands(id,type,payload,correlation_id)
                    VALUES(%s,'search',%s,%s)""",
                    (command_id, json.dumps(payload), request_id_var.get()),
                )
                db.execute(
                    "INSERT INTO jobs(id,command_id,total) VALUES(%s,%s,0)",
                    (job_id, command_id),
                )
            return SearchAccepted(
                query=body.query,
                status="queued",
                job_id=job_id.hex,
                total=0,
                pages_fetched=0,
                skipped=0,
                urls=[],
                result_url=f"/v1/jobs/{job_id.hex}",
            )
        payload["search_id"] = search_id.hex
        state = {
            "month": 0,
            "offset": 0,
            "pending": [],
            "seen": [],
            "fingerprints": [],
            "pages": 0,
            "skipped": 0,
            "job_ids": [],
            "warnings": [],
        }
        with self.connect() as db:
            db.execute(
                """INSERT INTO commands(id,type,payload,correlation_id)
                VALUES(%s,'search',%s,%s)""",
                (command_id, json.dumps(payload), request_id_var.get()),
            )
            db.execute(
                """INSERT INTO search_runs(id,request_json,state_json,status)
                VALUES(%s,%s,%s,'ready')""",
                (search_id, json.dumps(body.model_dump(mode="json")), json.dumps(state)),
            )
        return SearchAccepted(
            query=body.query,
            status="ready",
            total=0,
            pages_fetched=0,
            skipped=0,
            urls=[],
            search_id=search_id.hex,
            progress_url=f"/v1/search/runs/{search_id.hex}",
            continue_url=f"/v1/search/runs/{search_id.hex}/continue",
        )

    def continue_search(self, search_id: str) -> SearchAccepted:
        with self.connect() as db:
            run = db.execute(
                "SELECT request_json,status FROM search_runs WHERE id=%s FOR UPDATE", (search_id,)
            ).fetchone()
            if run is None:
                raise JobNotFoundError("Pencarian tidak ditemukan")
            body = SearchRequest.model_validate(run["request_json"])
            if run["status"] == "discovery_complete":
                return SearchAccepted(
                    query=body.query,
                    status="discovery_complete",
                    total=0,
                    pages_fetched=0,
                    skipped=0,
                    urls=[],
                    search_id=search_id,
                    progress_url=f"/v1/search/runs/{search_id}",
                )
            existing = db.execute(
                """SELECT 1 FROM commands WHERE type='search_continue'
                AND status IN ('queued','running') AND payload->>'search_id'=%s""",
                (search_id,),
            ).fetchone()
            if existing:
                raise HTTPException(409, "Checkpoint sudah diproses oleh request lain")
            command_id = uuid4()
            inserted = db.execute(
                """INSERT INTO commands(id,type,payload,correlation_id)
                VALUES(%s,'search_continue',%s,%s)
                ON CONFLICT DO NOTHING RETURNING id""",
                (
                    command_id,
                    json.dumps({"search_id": search_id, "command_id": command_id.hex}),
                    request_id_var.get(),
                ),
            ).fetchone()
            if inserted is None:
                raise HTTPException(409, "Checkpoint sudah diproses oleh request lain")
        return SearchAccepted(
            query=body.query,
            status="ready",
            total=0,
            pages_fetched=0,
            skipped=0,
            urls=[],
            search_id=search_id,
            progress_url=f"/v1/search/runs/{search_id}",
            continue_url=f"/v1/search/runs/{search_id}/continue",
        )

    def search_progress(self, search_id: str) -> SearchProgress:
        with self.connect() as db:
            row = db.execute("SELECT * FROM search_runs WHERE id=%s", (search_id,)).fetchone()
            if row is None:
                raise JobNotFoundError("Pencarian tidak ditemukan")
        body = SearchRequest.model_validate(row["request_json"])
        state = row["state_json"]
        start, end = body.start_date, body.end_date
        if start is None or end is None:
            raise JobNotFoundError("Pencarian ini tidak memiliki progres historis")
        months_total = (end.year - start.year) * 12 + end.month - start.month + 1
        current = None
        if state["month"] < months_total:
            year = start.year + (start.month - 1 + state["month"]) // 12
            month = (start.month - 1 + state["month"]) % 12 + 1
            current = start.replace(year=year, month=month, day=1)
        report = DateReport()
        for job_id in state.get("job_ids", []):
            job_report = self.get(job_id).date_report
            if job_report:
                for name in ("in_range", "out_of_range", "unknown_date", "failed", "pending"):
                    setattr(report, name, getattr(report, name) + getattr(job_report, name))
                for month, count in job_report.by_month.items():
                    report.by_month[month] = report.by_month.get(month, 0) + count
        complete = row["status"] == "discovery_complete"
        return SearchProgress(
            search_id=self._public_id(row["id"]),
            query=body.query,
            date_filter=DateFilter(start_date=start, end_date=end, timezone=body.timezone),
            status=row["status"],
            months_total=months_total,
            months_completed=min(state["month"], months_total),
            current_start_date=current,
            current_end_date=None,
            pages_fetched=state.get("pages", 0),
            requests_attempted=row["requests_attempted"],
            discovered=len(state.get("seen", [])),
            skipped=state.get("skipped", 0),
            job_ids=state.get("job_ids", []),
            date_report=report,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            continue_url=None if complete else f"/v1/search/runs/{search_id}/continue",
            warnings=state.get("warnings", []),
        )

    def recent_searches(self) -> list[SearchProgress]:
        with self.connect() as db:
            ids = [
                self._public_id(row["id"])
                for row in db.execute(
                    "SELECT id FROM search_runs ORDER BY created_at DESC LIMIT 20"
                )
            ]
        return [self.search_progress(search_id) for search_id in ids]

    @staticmethod
    def _duration(started: datetime | None, finished: datetime | None) -> int | None:
        if started is None:
            return None
        return max(0, round(((finished or datetime.now(UTC)) - started).total_seconds() * 1000))

    @staticmethod
    def _public_id(value) -> str:
        return str(value).replace("-", "")

    @staticmethod
    def _raise_worker_error(code: str | None, detail: str | None) -> None:
        message = detail or "Terjadi kesalahan internal"
        errors = {
            "unsafe_url": UnsafeUrlError,
            "robots_denied": RobotsDeniedError,
            "fetch_failed": FetchError,
            "extraction_failed": ExtractionError,
        }
        raise errors.get(code, FetchError)(message)
