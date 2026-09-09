"""Swagger request and response models."""

from __future__ import annotations

import datetime as dt
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_URLS_PER_JOB = 100

ReportTimezone = Literal["UTC", "Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"]


class ArticleTime(BaseModel):
    raw: str | None = None
    source: str | None = None
    date: dt.date | None = None
    local_datetime: str | None = None
    utc: datetime | None = None
    timezone: str | None = None
    precision: Literal["day", "minute", "second", "fraction", "unknown"] = "unknown"


class DateFilter(BaseModel):
    start_date: date
    end_date: date
    timezone: ReportTimezone = "Asia/Jakarta"

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date tidak boleh melewati end_date")
        if self.start_date.year < 1900 or self.end_date.year > 2100:
            raise ValueError("Tahun harus antara 1900 dan 2100")
        return self


class DateReport(BaseModel):
    in_range: int = 0
    out_of_range: int = 0
    unknown_date: int = 0
    failed: int = 0
    pending: int = 0
    by_month: dict[str, int] = Field(default_factory=dict)


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
    publication_time: ArticleTime = Field(default_factory=ArticleTime)
    modification_time: ArticleTime = Field(default_factory=ArticleTime)
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
    date_status: Literal["not_filtered", "pending", "in_range", "out_of_range", "unknown"] = (
        "not_filtered"
    )
    included: bool | None = None
    date_basis: Literal["report_timezone", "source_date"] | None = None


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
    date_filter: DateFilter | None = None
    date_report: DateReport | None = None
    search_id: str | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "query": "anak gunung krakatau",
                "max_articles": 50,
                "max_pages": 5,
                "start_date": "2024-09-09",
                "end_date": "2026-09-09",
                "timezone": "Asia/Jakarta",
            }
        },
    )
    query: str = Field(min_length=2, max_length=300)
    max_articles: int = Field(default=50, ge=1, le=50)
    max_pages: int = Field(
        default=5, ge=1, le=5, description="Batas request SerpAPI; tiap halaman memakai kuota"
    )
    start_date: date | None = Field(default=None, description="Tanggal terbit awal, inklusif")
    end_date: date | None = Field(default=None, description="Tanggal terbit akhir, inklusif")
    timezone: ReportTimezone = Field(
        default="Asia/Jakarta", description="Zona waktu untuk memeriksa tanggal publikasi"
    )

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date dan end_date harus diisi bersama")
        if self.start_date is not None:
            DateFilter(start_date=self.start_date, end_date=self.end_date, timezone=self.timezone)
            months = (
                (self.end_date.year - self.start_date.year) * 12
                + self.end_date.month
                - self.start_date.month
                + 1
            )
            if months > 120:
                raise ValueError("Maksimal 120 bulan kalender per pencarian")
        return self


class SearchAccepted(BaseModel):
    query: str
    status: str
    job_id: str | None = None
    total: int
    pages_fetched: int
    skipped: int
    urls: list[str]
    result_url: str | None = None
    search_id: str | None = None
    continue_url: str | None = None
    progress_url: str | None = None


class SearchProgress(BaseModel):
    search_id: str
    query: str
    date_filter: DateFilter
    status: Literal["ready", "running", "discovery_complete"]
    months_total: int
    months_completed: int
    current_start_date: date | None
    current_end_date: date | None
    pages_fetched: int
    requests_attempted: int
    discovered: int
    skipped: int
    job_ids: list[str]
    date_report: DateReport
    created_at: datetime
    updated_at: datetime
    continue_url: str | None
    warnings: list[str] = Field(default_factory=list)
    warning: str = "Selesai menelusuri hasil provider bukan jaminan seluruh arsip ditemukan."
