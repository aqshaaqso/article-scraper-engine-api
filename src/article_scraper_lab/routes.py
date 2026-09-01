"""Article scraper HTTP routes."""

from fastapi import APIRouter

from .dependencies import ApiKeyDep, JobManagerDep, ScraperServiceDep, SettingsDep
from .models import (
    MAX_URLS_PER_JOB,
    ApiError,
    ArticleResponse,
    BatchScrapeRequest,
    HealthResponse,
    JobAccepted,
    JobResponse,
    ScrapeRequest,
)

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
def get_job(job_id: str, manager: JobManagerDep, _api_key: ApiKeyDep) -> JobResponse:
    return manager.get(job_id)


@job_router.get("", summary="Lihat job terbaru")
def recent_jobs(manager: JobManagerDep, _api_key: ApiKeyDep) -> list[JobResponse]:
    return manager.recent()


system_router = APIRouter(tags=["System"])


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
