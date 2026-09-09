"""Resumable monthly discovery with atomic SQLite checkpoints and bounded calls."""

import calendar
import json
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException

from .article_dates import empty_date_report
from .errors import UnsafeUrlError
from .models import DateFilter, SearchAccepted, SearchProgress, SearchRequest
from .search import fetch_news_page


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows = []
    while start <= end:
        last = min(
            end, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        )
        windows.append((start, last))
        start = last + timedelta(days=1)
    return windows


class HistoricalSearch:
    def __init__(self, settings, service, manager):
        self.settings, self.service, self.manager = settings, service, manager
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS search_runs (
                id TEXT PRIMARY KEY, request_json TEXT NOT NULL, state_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0,
                requests_attempted INTEGER NOT NULL DEFAULT 0
            )""")

    def connect(self):
        db = sqlite3.connect(self.settings.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, body: SearchRequest) -> str:
        search_id = uuid4().hex
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
            "domains": list(self.settings.allowed_domains),
        }
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO search_runs(id,request_json,state_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                (search_id, body.model_dump_json(), json.dumps(state), now, now),
            )
        return search_id

    def read(self, search_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM search_runs WHERE id=?", (search_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Pencarian tidak ditemukan")
        return row

    def progress(self, search_id: str) -> SearchProgress:
        row = self.read(search_id)
        body = SearchRequest.model_validate_json(row["request_json"])
        state = json.loads(row["state_json"])
        windows = month_windows(body.start_date, body.end_date)
        complete = state["month"] == len(windows) and not state["pending"]
        report = empty_date_report(
            DateFilter(
                start_date=body.start_date,
                end_date=body.end_date,
                timezone=body.timezone,
            )
        )
        for job_id in state["job_ids"]:
            part = self.manager.get(job_id).date_report
            if part:
                for field in ("in_range", "out_of_range", "unknown_date", "failed", "pending"):
                    setattr(report, field, getattr(report, field) + getattr(part, field))
                for month, count in part.by_month.items():
                    report.by_month[month] = report.by_month.get(month, 0) + count
        # Pending rows still belong to the previous fetched page, not the next month.
        completed_months = state["month"] - int(bool(state["pending"]) and state["offset"] == 0)
        current = windows[min(completed_months, len(windows) - 1)] if not complete else (None, None)
        return SearchProgress(
            search_id=search_id,
            query=body.query,
            date_filter=DateFilter(
                start_date=body.start_date, end_date=body.end_date, timezone=body.timezone
            ),
            status="discovery_complete"
            if complete
            else ("running" if row["lease_until"] > time.time() else "ready"),
            months_total=len(windows),
            months_completed=completed_months,
            current_start_date=current[0],
            current_end_date=current[1],
            pages_fetched=state["pages"],
            requests_attempted=row["requests_attempted"],
            discovered=len(state["seen"]),
            skipped=state["skipped"],
            job_ids=state["job_ids"],
            date_report=report,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            continue_url=None if complete else f"/v1/search/runs/{search_id}/continue",
            warnings=state["warnings"],
        )

    def advance(self, search_id: str) -> SearchAccepted:
        if not self.settings.serpapi_api_key or not self.settings.allowed_domains:
            raise HTTPException(503, "SERPAPI_API_KEY dan ALLOWED_DOMAINS wajib diisi")
        token = uuid4().hex
        self.read(search_id)
        with self.connect() as db:
            changed = db.execute(
                "UPDATE search_runs SET lease_token=?,lease_until=? WHERE id=? AND lease_until<=?",
                (token, time.time() + 600, search_id, time.time()),
            ).rowcount
        if not changed:
            raise HTTPException(409, "Pencarian ini sedang diproses; periksa progres dahulu")
        try:
            return self._advance(search_id, token)
        except HTTPException as error:
            raise HTTPException(
                error.status_code,
                {
                    "message": error.detail,
                    "search_id": search_id,
                    "progress_url": f"/v1/search/runs/{search_id}",
                    "continue_url": f"/v1/search/runs/{search_id}/continue",
                },
            ) from None
        finally:
            with self.connect() as db:
                db.execute(
                    "UPDATE search_runs SET lease_token=NULL,lease_until=0 "
                    "WHERE id=? AND lease_token=?",
                    (search_id, token),
                )

    def _advance(self, search_id, token):
        row = self.read(search_id)
        body = SearchRequest.model_validate_json(row["request_json"])
        state = json.loads(row["state_json"])
        if state["domains"] != list(self.settings.allowed_domains):
            raise HTTPException(409, "Allowlist berubah; buat pencarian baru")
        windows = month_windows(body.start_date, body.end_date)
        seen = set(state["seen"])
        urls, pages, skipped = [], 0, 0
        sites = " OR ".join(f"site:{domain}" for domain in state["domains"])
        query = f"{body.query} ({sites})"
        while len(urls) < body.max_articles:
            if state["pending"]:
                url = state["pending"].pop(0)
                if url in seen:
                    skipped += 1
                    continue
                seen.add(url)
                urls.append(url)
                continue
            if state["month"] >= len(windows) or pages >= body.max_pages:
                break
            lower, upper = windows[state["month"]]
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE search_runs SET requests_attempted=requests_attempted+1,lease_until=? "
                    "WHERE id=? AND lease_token=?",
                    (time.time() + 600, search_id, token),
                ).rowcount
            if not updated:
                raise HTTPException(409, "Checkpoint sudah diproses oleh request lain")
            # Broaden discovery at timezone boundaries; exact filtering uses article metadata.
            payload = fetch_news_page(
                self.settings.serpapi_api_key,
                query,
                state["offset"],
                lower - timedelta(days=1),
                upper + timedelta(days=1),
            )
            pages += 1
            rows = payload.get("organic_results", [])
            fingerprint = sha256(
                json.dumps(
                    [r.get("link") for r in rows if isinstance(r, dict)],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            repeated = fingerprint in state["fingerprints"]
            if repeated:
                state["warnings"].append(f"{lower:%Y-%m}: provider mengulang halaman; dihentikan")
            else:
                state["fingerprints"].append(fingerprint)
                for entry in rows:
                    link = entry.get("link") if isinstance(entry, dict) else None
                    if not isinstance(link, str):
                        skipped += 1
                        continue
                    try:
                        target = self.service.validate_url(link)
                    except UnsafeUrlError:
                        skipped += 1
                        continue
                    path = urlsplit(target.url).path.lower()
                    if path == "/" or path.startswith(
                        ("/tag/", "/tags/", "/topic/", "/topik/", "/search", "/indeks", "/index")
                    ):
                        skipped += 1
                    else:
                        state["pending"].append(target.url)
            pagination = payload.get("serpapi_pagination")
            if (
                repeated
                or not rows
                or not isinstance(pagination, dict)
                or not pagination.get("next")
            ):
                state["month"] += 1
                state["offset"] = 0
                state["fingerprints"] = []
            else:
                state["offset"] += 10
        state["seen"] = sorted(seen)
        state["pages"] += pages
        state["skipped"] += skipped
        criteria = DateFilter(
            start_date=body.start_date, end_date=body.end_date, timezone=body.timezone
        )

        def checkpoint(db, job_id):
            if job_id:
                state["job_ids"].append(job_id)
            count = db.execute(
                "UPDATE search_runs SET state_json=?,updated_at=? WHERE id=? AND lease_token=?",
                (json.dumps(state), datetime.now(UTC).isoformat(), search_id, token),
            ).rowcount
            if not count:
                raise HTTPException(409, "Checkpoint sudah berubah; periksa progres")

        job_id = self.manager.commit_search_batch(
            urls,
            {"search_id": search_id, "date_filter": criteria.model_dump(mode="json")},
            checkpoint,
        )
        complete = state["month"] == len(windows) and not state["pending"]
        return SearchAccepted(
            query=body.query,
            status="queued" if job_id else ("discovery_complete" if complete else "ready"),
            job_id=job_id,
            total=len(urls),
            pages_fetched=pages,
            skipped=skipped,
            urls=urls,
            result_url=f"/v1/jobs/{job_id}" if job_id else None,
            search_id=search_id,
            continue_url=None if complete else f"/v1/search/runs/{search_id}/continue",
            progress_url=f"/v1/search/runs/{search_id}",
        )

    def recent(self) -> list[SearchProgress]:
        with self.connect() as db:
            ids = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM search_runs ORDER BY created_at DESC LIMIT 20"
                )
            ]
        return [self.progress(search_id) for search_id in ids]
