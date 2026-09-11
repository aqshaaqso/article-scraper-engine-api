"""Article scraper HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .dependencies import ApiKeyDep, PostgresGatewayDep, SettingsDep
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
    gateway: PostgresGatewayDep,
    _api_key: ApiKeyDep,
) -> ArticleResponse:
    return gateway.scrape(body.url)


job_router = APIRouter(prefix="/v1/jobs", tags=["Async jobs"])


@job_router.post("", status_code=202, summary="Masukkan URL ke antrean asinkron")
def create_job(
    body: BatchScrapeRequest,
    gateway: PostgresGatewayDep,
    _api_key: ApiKeyDep,
) -> JobAccepted:
    return gateway.submit(body.urls)


@job_router.get("/{job_id}", summary="Pantau progres dan hasil job")
def get_job(
    job_id: UUID,
    gateway: PostgresGatewayDep,
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
    return _filter_job_items(gateway.get(job_id.hex), include_excluded)


@job_router.get("", summary="Lihat job terbaru")
def recent_jobs(
    gateway: PostgresGatewayDep,
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
    return [_filter_job_items(job, include_excluded) for job in gateway.recent()]


def _filter_job_items(job: JobResponse, include_excluded: bool) -> JobResponse:
    if include_excluded or job.date_filter is None:
        return job
    return job.model_copy(update={"items": [item for item in job.items if item.included is True]})


system_router = APIRouter(tags=["System"])

search_router = APIRouter(prefix="/v1/search", tags=["Search news"])

SEARCH_REQUEST_EXAMPLES = {
    "general": {
        "summary": "Tanpa filter tanggal",
        "description": "Cari berita umum tanpa membatasi tanggal publikasi.",
        "value": {"query": "Indonesia", "max_articles": 10, "max_pages": 1},
    },
    "day": {
        "summary": "Satu hari",
        "description": "Day memakai bulan dan tahun berjalan.",
        "value": {
            "query": "Indonesia",
            "day": 1,
            "timezone": "Asia/Jakarta",
            "max_articles": 10,
            "max_pages": 1,
        },
    },
    "month": {
        "summary": "Satu bulan",
        "description": "Month memakai tahun berjalan.",
        "value": {
            "query": "Indonesia",
            "month": 1,
            "timezone": "Asia/Jakarta",
            "max_articles": 10,
            "max_pages": 1,
        },
    },
    "year": {
        "summary": "Satu tahun",
        "value": {
            "query": "Indonesia",
            "year": 2025,
            "timezone": "Asia/Jakarta",
            "max_articles": 10,
            "max_pages": 1,
        },
    },
    "start_date": {
        "summary": "Sejak tanggal tertentu",
        "description": "End date otomatis menjadi hari ini.",
        "value": {
            "query": "Indonesia",
            "start_date": "2025-07-15",
            "timezone": "Asia/Jakarta",
            "max_articles": 10,
            "max_pages": 1,
        },
    },
    "range": {
        "summary": "Rentang eksplisit",
        "value": {
            "query": "Indonesia",
            "start_date": "2025-07-15",
            "end_date": "2025-09-10",
            "timezone": "Asia/Jakarta",
            "max_articles": 10,
            "max_pages": 1,
        },
    },
}


@search_router.post(
    "/jobs",
    status_code=202,
    summary="Cari berita lalu scrape otomatis",
    description="Cari URL via SerpAPI, filter domain, lalu antrekan scraping. "
    "Filter tanggal opsional: day memakai bulan berjalan, month memakai tahun berjalan, "
    "year memakai 12 bulan penuh, dan start_date tanpa end_date berlaku sampai hari ini. "
    "Tanggal masa depan ditolak; month/year berjalan dibatasi sampai hari ini. "
    "Rentang bertanggal diproses bertahap per bulan dan bisa dilanjutkan. "
    "max_pages dan max_articles membatasi setiap panggilan, bukan seluruh periode. "
    "Ambil JSON artikel lengkap melalui result_url / GET /v1/jobs/{job_id}. "
    "Secara default result_url hanya menampilkan artikel yang tanggal publikasinya masuk rentang.",
)
def search_news(
    body: Annotated[SearchRequest, Body(openapi_examples=SEARCH_REQUEST_EXAMPLES)],
    gateway: PostgresGatewayDep,
    _api_key: ApiKeyDep,
) -> SearchAccepted:
    return gateway.submit_search(body)


@search_router.get("/runs", summary="Lihat progres pencarian historis terbaru")
def recent_searches(
    gateway: PostgresGatewayDep,
    _api_key: ApiKeyDep,
) -> list[SearchProgress]:
    return gateway.recent_searches()


@search_router.get("/runs/{search_id}", summary="Progres bulanan dan laporan tanggal artikel")
def search_progress(
    search_id: UUID,
    gateway: PostgresGatewayDep,
    _api_key: ApiKeyDep,
) -> SearchProgress:
    return gateway.search_progress(search_id.hex)


@search_router.post(
    "/runs/{search_id}/continue",
    status_code=202,
    summary="Lanjutkan pencarian dari progres tersimpan",
    description="Tanpa request body. Menggunakan kata kunci, rentang dan batas permintaan awal. "
    "Setiap halaman provider dapat memakai kuota. Setelah discovery_complete, "
    "panggilan ini tidak melakukan pencarian atau membuat job baru.",
)
def continue_search(
    search_id: UUID,
    gateway: PostgresGatewayDep,
    _api_key: ApiKeyDep,
) -> SearchAccepted:
    return gateway.continue_search(search_id.hex)


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
def readiness(gateway: PostgresGatewayDep) -> ReadinessResponse:
    if not gateway.health():
        raise HTTPException(503, "PostgreSQL tidak tersedia")
    return ReadinessResponse(
        status="ready",
        database="postgresql",
        schema_version=SCHEMA_VERSION,
    )


@system_router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics(gateway: PostgresGatewayDep, _api_key: ApiKeyDep) -> str:
    return gateway.metrics()
