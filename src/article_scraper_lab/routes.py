"""Article scraper HTTP routes."""

from fastapi import APIRouter

from .dependencies import ApiKeyDep, JobManagerDep, ScraperServiceDep, SettingsDep
from .historical_search import HistoricalSearch
from .models import (
    MAX_URLS_PER_JOB,
    ApiError,
    ArticleResponse,
    BatchScrapeRequest,
    HealthResponse,
    JobAccepted,
    JobResponse,
    ScrapeRequest,
    SearchAccepted,
    SearchProgress,
    SearchRequest,
)
from .search import search_and_submit

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

search_router = APIRouter(prefix="/v1/search", tags=["Search news"])


@search_router.post(
    "/jobs",
    status_code=202,
    summary="Cari berita lalu scrape otomatis",
    description="Cari URL via SerpAPI, filter domain, lalu antrekan scraping. "
    "Isi start_date dan end_date untuk pencarian bertahap per bulan yang bisa dilanjutkan. "
    "max_pages dan max_articles membatasi setiap panggilan, bukan seluruh periode. "
    "Ambil JSON artikel lengkap melalui result_url / GET /v1/jobs/{job_id}. "
    "Untuk hasil dalam rentang, pilih items dengan included=true.",
)
def search_news(
    body: SearchRequest,
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> SearchAccepted:
    return search_and_submit(body, settings, service, manager)


@search_router.get("/runs", summary="Lihat progres pencarian historis terbaru")
def recent_searches(
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> list[SearchProgress]:
    return HistoricalSearch(settings, service, manager).recent()


@search_router.get("/runs/{search_id}", summary="Progres bulanan dan laporan tanggal artikel")
def search_progress(
    search_id: str,
    settings: SettingsDep,
    service: ScraperServiceDep,
    manager: JobManagerDep,
    _api_key: ApiKeyDep,
) -> SearchProgress:
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
