"""Swagger request and response models."""

from __future__ import annotations

import calendar
import datetime as dt
from datetime import date, datetime
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    field_validator,
    model_validator,
)

MAX_URLS_PER_JOB = 100

ReportTimezone = Literal["UTC", "Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"]

REPORT_TIMEZONE_OFFSETS = {
    "UTC": 0,
    "Asia/Jakarta": 7,
    "Asia/Makassar": 8,
    "Asia/Jayapura": 9,
}


def _today_in_timezone(name: ReportTimezone) -> date:
    zone = dt.timezone(dt.timedelta(hours=REPORT_TIMEZONE_OFFSETS[name]))
    return datetime.now(zone).date()


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


class RequestedDateFilter(BaseModel):
    mode: Literal["day", "month", "year", "start_date", "range", "legacy_range"]
    day: int | None = None
    month: int | None = None
    year: int | None = None
    start_date: date | None = None
    end_date: date | None = None


def requested_date_filter_from_storage(
    payload: dict[str, object], criteria: DateFilter | None
) -> RequestedDateFilter | None:
    stored = payload.get("requested_date_filter")
    if stored is not None:
        return RequestedDateFilter.model_validate(stored)
    if criteria is None:
        return None
    return RequestedDateFilter(
        mode="legacy_range",
        start_date=criteria.start_date,
        end_date=criteria.end_date,
    )


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


class ReadinessResponse(BaseModel):
    status: str
    database: str
    schema_version: int


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
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(
        default=None,
        description="Durasi sejak worker pertama mulai sampai job selesai atau saat ini",
    )
    items: list[JobItemResponse] = Field(default_factory=list)
    requested_date_filter: RequestedDateFilter | None = None
    date_filter: DateFilter | None = None
    date_report: DateReport | None = None
    search_id: str | None = None


class SearchRequest(BaseModel):
    _requested_date_filter: RequestedDateFilter | None = PrivateAttr(default=None)

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "query": "anak gunung krakatau",
                "max_articles": 50,
                "max_pages": 5,
                "month": 9,
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
    day: int | None = Field(
        default=None,
        ge=1,
        le=31,
        description="Satu tanggal pada bulan dan tahun berjalan",
    )
    month: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description="Satu bulan penuh pada tahun berjalan",
    )
    year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Satu tahun penuh (12 bulan)",
    )
    timezone: ReportTimezone = Field(
        default="Asia/Jakarta", description="Zona waktu untuk memeriksa tanggal publikasi"
    )

    @model_validator(mode="after")
    def validate_dates(self, info: ValidationInfo) -> Self:
        selectors = [self.day is not None, self.month is not None, self.year is not None]
        if sum(selectors) > 1:
            raise ValueError("day, month, dan year tidak boleh digabungkan")
        if any(selectors) and (self.start_date is not None or self.end_date is not None):
            raise ValueError(
                "Filter day/month/year tidak boleh digabung dengan start_date/end_date"
            )

        if self.day is not None:
            requested = RequestedDateFilter(mode="day", day=self.day)
        elif self.month is not None:
            requested = RequestedDateFilter(mode="month", month=self.month)
        elif self.year is not None:
            requested = RequestedDateFilter(mode="year", year=self.year)
        elif self.start_date is not None and self.end_date is not None:
            requested = RequestedDateFilter(
                mode="range", start_date=self.start_date, end_date=self.end_date
            )
        elif self.start_date is not None:
            requested = RequestedDateFilter(mode="start_date", start_date=self.start_date)
        else:
            requested = None

        today = _today_in_timezone(self.timezone)
        from_storage = bool(info.context and info.context.get("from_storage"))
        start, end = self.start_date, self.end_date
        if self.day is not None:
            last_day = calendar.monthrange(today.year, today.month)[1]
            if self.day > last_day:
                raise ValueError("day tidak tersedia pada bulan berjalan")
            start = end = date(today.year, today.month, self.day)
            if not from_storage and start > today:
                raise ValueError("day tidak boleh berada setelah hari ini")
        elif self.month is not None:
            if not from_storage and self.month > today.month:
                raise ValueError("month tidak boleh berada setelah bulan berjalan")
            start = date(today.year, self.month, 1)
            end = date(today.year, self.month, calendar.monthrange(today.year, self.month)[1])
            if not from_storage and self.month == today.month:
                end = today
        elif self.year is not None:
            if not from_storage and self.year > today.year:
                raise ValueError("year tidak boleh berada setelah tahun berjalan")
            start = date(self.year, 1, 1)
            end = today if not from_storage and self.year == today.year else date(self.year, 12, 31)
        elif start is not None and end is None:
            if not from_storage and start > today:
                raise ValueError("start_date tidak boleh berada setelah hari ini")
            end = today
        elif start is None and end is not None:
            raise ValueError("end_date membutuhkan start_date")

        if not from_storage and end is not None and end > today:
            raise ValueError("end_date tidak boleh berada setelah hari ini")

        if start is not None and end is not None:
            DateFilter(start_date=start, end_date=end, timezone=self.timezone)
            object.__setattr__(self, "start_date", start)
            object.__setattr__(self, "end_date", end)
            # Persist a canonical range so Go continuations never depend on a later current date.
            object.__setattr__(self, "day", None)
            object.__setattr__(self, "month", None)
            object.__setattr__(self, "year", None)
            months = (end.year - start.year) * 12 + end.month - start.month + 1
            if months > 120:
                raise ValueError("Maksimal 120 bulan kalender per pencarian")
        self._requested_date_filter = requested
        return self

    @property
    def requested_date_filter(self) -> RequestedDateFilter | None:
        return self._requested_date_filter

    @property
    def includes_future_date(self) -> bool:
        today = _today_in_timezone(self.timezone)
        return bool(
            (self.start_date is not None and self.start_date > today)
            or (self.end_date is not None and self.end_date > today)
        )


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
    requested_date_filter: RequestedDateFilter | None = None
    date_filter: DateFilter | None = None


class SearchProgress(BaseModel):
    search_id: str
    query: str
    date_filter: DateFilter
    requested_date_filter: RequestedDateFilter
    status: Literal["ready", "running", "discovery_complete", "failed"]
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
