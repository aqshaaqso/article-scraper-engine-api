"""Article scraper HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .dependencies import ApiKeyDep, JobManagerDep, ScraperServiceDep, SettingsDep
from .models import (
    MAX_URLS_PER_JOB,
    ApiError,
    ArticleResponse,
    BatchScrapeRequest,
    HealthResponse,
    JobAccepted,
    JobResponse,
    ReadinessResponse,
    ScrapeRequest,
    SearchAccepted,
    SearchProgress,
    SearchRequest,
)
from .postgres_gateway import SCHEMA_VERSION

router = APIRouter(prefix="/v1/articles", tags=["Articles"])


@router.post(
    "/scrape",
    summary="Scrape satu artikel berita",
    response_description="Metadata dan isi utama artikel yang sudah diekstrak",
    responses={
        403: {"model": ApiError, "description": "Ditolak oleh robots.txt"},
        422: {"model": ApiError, "description": "URL tidak aman atau ekstraksi gagal"},
        502: {"model": ApiError, "description": "Halaman gagal diambil"},
    },
)
def scrape_article(
    body: ScrapeRequest,
    service: ScraperServiceDep,
    _api_key: ApiKeyDep,
) -> ArticleResponse:
    return service.scrape(body.url)


job_router = APIRouter(prefix="/v1/jobs", tags=["Async jobs"])


@job_router.post("", status_code=202, summary="Masukkan URL ke antrean asinkron")
def create_job(
    body: BatchScrapeRequest,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> JobAccepted:
    return manager.submit(body.urls)


@job_router.get("/{job_id}", summary="Pantau progres dan hasil job")
def get_job(
    job_id: str,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
    include_excluded: Annotated[
        bool,
        Query(
            description=(
                "Khusus job dengan filter tanggal: tampilkan juga artikel di luar rentang, "
                "tanggal tidak diketahui, gagal, dan yang masih diproses untuk audit"
            )
        ),
    ] = False,
) -> JobResponse:
    return _filter_job_items(manager.get(job_id), include_excluded)


@job_router.get("", summary="Lihat job terbaru")
def recent_jobs(
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
    include_excluded: Annotated[
        bool,
        Query(
            description=(
                "Khusus job dengan filter tanggal: tampilkan juga item yang tidak lolos "
                "filter untuk audit"
            )
        ),
    ] = False,
) -> list[JobResponse]:
    return [_filter_job_items(job, include_excluded) for job in manager.recent()]


def _filter_job_items(job: JobResponse, include_excluded: bool) -> JobResponse:
    if include_excluded or job.date_filter is None:
        return job
    return job.model_copy(update={"items": [item for item in job.items if item.included is True]})


system_router = APIRouter(tags=["System"])

search_router = APIRouter(prefix="/v1/search", tags=["Search news"])


@search_router.post(
    "/jobs",
    status_code=202,
    summary="Cari berita lalu scrape otomatis",
    description="Cari URL via SerpAPI, filter domain, lalu antrekan scraping. "
    "Filter tanggal opsional: day memakai bulan berjalan, month memakai tahun berjalan, "
    "year memakai 12 bulan penuh, dan start_date tanpa end_date berlaku sampai hari ini. "
    "Rentang bertanggal diproses bertahap per bulan dan bisa dilanjutkan. "
    "max_pages dan max_articles membatasi setiap panggilan, bukan seluruh periode. "
    "Ambil JSON artikel lengkap melalui result_url / GET /v1/jobs/{job_id}. "
    "Secara default result_url hanya menampilkan artikel yang tanggal publikasinya masuk rentang.",
)
def search_news(
    body: SearchRequest,
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> SearchAccepted:
    if settings.database_url:
        return manager.submit_search(body)  # type: ignore[attr-defined]
    from .search import search_and_submit

    return search_and_submit(body, settings, service, manager)


@search_router.get("/runs", summary="Lihat progres pencarian historis terbaru")
def recent_searches(
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> list[SearchProgress]:
    if settings.database_url:
        return manager.recent_searches()  # type: ignore[attr-defined]
    from .historical_search import HistoricalSearch

    return HistoricalSearch(settings, service, manager).recent()


@search_router.get("/runs/{search_id}", summary="Progres bulanan dan laporan tanggal artikel")
def search_progress(
    search_id: str,
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> SearchProgress:
    if settings.database_url:
        return manager.search_progress(search_id)  # type: ignore[attr-defined]
    from .historical_search import HistoricalSearch

    return HistoricalSearch(settings, service, manager).progress(search_id)


@search_router.post(
    "/runs/{search_id}/continue",
    status_code=202,
    summary="Lanjutkan pencarian dari progres tersimpan",
    description="Tanpa request body. Menggunakan kata kunci, rentang dan batas permintaan awal. "
    "Setiap halaman provider dapat memakai kuota. Setelah discovery_complete, "
    "panggilan ini tidak melakukan pencarian atau membuat job baru.",
)
def continue_search(
    search_id: str,
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> SearchAccepted:
    if settings.database_url:
        return manager.continue_search(search_id)  # type: ignore[attr-defined]
    from .historical_search import HistoricalSearch

    return HistoricalSearch(settings, service, manager).advance(search_id)


@system_router.get("/health", summary="Periksa konfigurasi scraper")
def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        allow_http=settings.allow_http,
        respect_robots=settings.respect_robots,
        domain_allowlist_enabled=bool(settings.allowed_domains),
        api_key_required=settings.api_key is not None,
        max_urls_per_job=MAX_URLS_PER_JOB,
    )


@system_router.get("/ready", summary="Periksa kesiapan middleware dan database")
def readiness(settings: SettingsDep, manager: JobManagerDep) -> ReadinessResponse:
    if settings.database_url and not manager.health():  # type: ignore[attr-defined]
        raise HTTPException(503, "PostgreSQL tidak tersedia")
    return ReadinessResponse(
        status="ready",
        database="postgresql" if settings.database_url else "sqlite-oracle",
        schema_version=SCHEMA_VERSION if settings.database_url else 0,
    )


@system_router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics(settings: SettingsDep, manager: JobManagerDep, _api_key: ApiKeyDep) -> str:
    if not settings.database_url:
        raise HTTPException(503, "Metrik production hanya tersedia pada mode PostgreSQL")
    return manager.metrics()  # type: ignore[attr-defined]
