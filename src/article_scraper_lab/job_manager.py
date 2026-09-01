"""Asynchronous worker pool for scraper jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .errors import ExtractionError, FetchError, RobotsDeniedError, UnsafeUrlError
from .job_store import JobStore
from .models import JobAccepted, JobResponse
from .service import ArticleScraperService


class JobManager:
    def __init__(
        self,
        store: JobStore,
        service: ArticleScraperService,
        worker_count: int,
    ) -> None:
        self._store = store
        self._service = service
        self._worker_count = worker_count
        self._executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="scraper")

    def start(self) -> None:
        self._store.initialize()
        for item in self._store.queued_items():
            self._schedule(item)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def submit(self, urls: list[str]) -> JobAccepted:
        targets = [self._service.validate_url(url) for url in urls]
        job_id = self._store.create(urls, targets)
        for item in self._store.queued_items(job_id):
            self._schedule(item)
        return JobAccepted(
            job_id=job_id, status="queued", total=len(urls), worker_count=self._worker_count
        )

    def get(self, job_id: str) -> JobResponse:
        return self._store.get(job_id)

    def recent(self) -> list[JobResponse]:
        return self._store.recent()

    @property
    def worker_count(self) -> int:
        return self._worker_count

    def _schedule(self, item: object) -> None:
        self._executor.submit(self._process, dict(item))

    def _process(self, item: dict[str, object]) -> None:
        item_id, job_id = int(item["id"]), str(item["job_id"])
        self._store.mark_running(item_id, job_id)
        try:
            # Validation happens again inside scrape immediately before network use.
            article = self._service.scrape(str(item["normalized_url"]))
        except UnsafeUrlError as error:
            self._store.finish_item(
                item_id, job_id, error_code="unsafe_url", error_detail=str(error)
            )
        except RobotsDeniedError as error:
            self._store.finish_item(
                item_id, job_id, error_code="robots_denied", error_detail=str(error)
            )
        except FetchError as error:
            self._store.finish_item(
                item_id, job_id, error_code="fetch_failed", error_detail=str(error)
            )
        except ExtractionError as error:
            self._store.finish_item(
                item_id, job_id, error_code="extraction_failed", error_detail=str(error)
            )
        except Exception:
            self._store.finish_item(
                item_id,
                job_id,
                error_code="internal_error",
                error_detail="Terjadi kesalahan internal",
            )
        else:
            self._store.finish_item(item_id, job_id, article=article)
