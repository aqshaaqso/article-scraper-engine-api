"""Swagger request and response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_URLS_PER_JOB = 100


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"url": "https://example.com/news/article"}}
    )

    url: str = Field(min_length=8, max_length=2048, description="URL HTTPS artikel berita")

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.strip()


class ArticleResponse(BaseModel):
    status: str = "success"
    source_url: str
    final_url: str
    canonical_url: str
    domain: str
    title: str
    author: str | None = None
    published_at: str | None = None
    modified_at: str | None = None
    source: str | None = None
    section: str | None = None
    description: str | None = None
    image_url: str | None = None
    content: str
    word_count: int
    content_hash: str
    robots_status: str
    fetched_at: datetime


class HealthResponse(BaseModel):
    status: str
    allow_http: bool
    respect_robots: bool
    domain_allowlist_enabled: bool
    api_key_required: bool
    max_urls_per_job: int


class ApiError(BaseModel):
    error: str
    detail: str


class BatchScrapeRequest(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        max_length=MAX_URLS_PER_JOB,
        description=f"Daftar URL; maksimal {MAX_URLS_PER_JOB} URL per job",
    )

    @field_validator("urls")
    @classmethod
    def normalize_urls(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if not normalized:
            raise ValueError("Minimal satu URL harus diisi")
        return normalized


class JobAccepted(BaseModel):
    job_id: str
    status: str
    total: int
    worker_count: int


class JobItemResponse(BaseModel):
    position: int
    url: str
    status: str
    error_code: str | None = None
    error_detail: str | None = None
    article: ArticleResponse | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    succeeded: int
    failed: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(
        default=None,
        description="Durasi sejak worker pertama mulai sampai job selesai atau saat ini",
    )
    items: list[JobItemResponse] = Field(default_factory=list)
