"""Small persistent SQLite store for scraper jobs."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import JobNotFoundError
from .models import ArticleResponse, JobItemResponse, JobResponse
from .security import UrlTarget


def _now() -> str:
    return datetime.now(UTC).isoformat()


def calculate_duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if started_at is None:
        return None
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at) if finished_at else datetime.now(UTC)
    return max(0, round((finished - started).total_seconds() * 1000))


class JobStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, total INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0, succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS job_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    position INTEGER NOT NULL, url TEXT NOT NULL, normalized_url TEXT NOT NULL,
                    domain TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    error_code TEXT, error_detail TEXT, article_json TEXT,
                    started_at TEXT, finished_at TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id, position);
                """
            )

    def create(self, urls: list[str], targets: list[UrlTarget]) -> str:
        job_id = uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO jobs(id,status,total,created_at) VALUES(?,?,?,?)",
                (job_id, "queued", len(urls), _now()),
            )
            db.executemany(
                """INSERT INTO job_items(job_id,position,url,normalized_url,domain)
                VALUES(?,?,?,?,?)""",
                [
                    (job_id, index, raw, target.url, target.hostname)
                    for index, (raw, target) in enumerate(zip(urls, targets, strict=True))
                ],
            )
        return job_id

    def queued_items(self, job_id: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM job_items WHERE status IN ('queued','running')"
        params: tuple[str, ...] = ()
        if job_id:
            query += " AND job_id=?"
            params = (job_id,)
        query += " ORDER BY id"
        with self._connect() as db:
            return list(db.execute(query, params))

    def mark_running(self, item_id: int, job_id: str) -> None:
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE job_items SET status='running',started_at=? WHERE id=?",
                (now, item_id),
            )
            db.execute(
                "UPDATE jobs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?",
                (now, job_id),
            )

    def finish_item(
        self,
        item_id: int,
        job_id: str,
        *,
        article: ArticleResponse | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        status = "success" if article else "failed"
        payload = article.model_dump_json() if article else None
        with self._connect() as db:
            db.execute(
                """UPDATE job_items SET status=?,error_code=?,error_detail=?,article_json=?,
                finished_at=? WHERE id=?""",
                (status, error_code, error_detail, payload, _now(), item_id),
            )
            counts = db.execute(
                """SELECT COUNT(*) total,
                SUM(status IN ('success','failed')) completed,
                SUM(status='success') succeeded, SUM(status='failed') failed
                FROM job_items WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            done = counts["completed"] == counts["total"]
            db.execute(
                """UPDATE jobs SET status=?,completed=?,succeeded=?,failed=?,
                finished_at=? WHERE id=?""",
                (
                    "completed" if done else "running",
                    counts["completed"],
                    counts["succeeded"],
                    counts["failed"],
                    _now() if done else None,
                    job_id,
                ),
            )

    def get(self, job_id: str) -> JobResponse:
        with self._connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise JobNotFoundError("Job tidak ditemukan")
            rows = db.execute(
                "SELECT * FROM job_items WHERE job_id=? ORDER BY position", (job_id,)
            ).fetchall()
        items = [
            JobItemResponse(
                position=row["position"],
                url=row["url"],
                status=row["status"],
                error_code=row["error_code"],
                error_detail=row["error_detail"],
                article=(
                    ArticleResponse.model_validate(json.loads(row["article_json"]))
                    if row["article_json"]
                    else None
                ),
            )
            for row in rows
        ]
        return JobResponse(
            job_id=job["id"],
            status=job["status"],
            total=job["total"],
            completed=job["completed"],
            succeeded=job["succeeded"],
            failed=job["failed"],
            created_at=job["created_at"],
            started_at=job["started_at"],
            finished_at=job["finished_at"],
            duration_ms=calculate_duration_ms(job["started_at"], job["finished_at"]),
            items=items,
        )

    def recent(self, limit: int = 20) -> list[JobResponse]:
        with self._connect() as db:
            ids = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            ]
        return [self.get(job_id) for job_id in ids]
