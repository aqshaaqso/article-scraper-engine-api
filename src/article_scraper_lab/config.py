"""Environment-backed scraper settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} harus true atau false")
    return raw == "true"


def _positive_integer(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} harus berupa angka bulat positif") from exc
    if value <= 0:
        raise ValueError(f"{name} harus lebih besar dari 0")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} harus berupa angka positif") from exc
    if value <= 0:
        raise ValueError(f"{name} harus lebih besar dari 0")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    timeout_seconds: float
    max_html_bytes: int
    max_redirects: int
    min_word_count: int
    user_agent: str
    allow_http: bool
    respect_robots: bool
    robots_fail_closed: bool
    allowed_domains: tuple[str, ...]
    worker_count: int
    domain_delay_seconds: float
    database_path: Path
    api_key: str | None

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        api_key = os.getenv("SCRAPER_API_KEY", "").strip() or None
        if _boolean("REQUIRE_API_KEY", False) and api_key is None:
            raise ValueError("SCRAPER_API_KEY wajib diisi ketika REQUIRE_API_KEY=true")
        domains = tuple(
            domain.strip().lower().rstrip(".")
            for domain in os.getenv("ALLOWED_DOMAINS", "").split(",")
            if domain.strip()
        )
        database_path = Path(os.getenv("DATABASE_PATH", "data/article_scraper.db"))
        if not database_path.is_absolute():
            database_path = Path(__file__).resolve().parents[2] / database_path
        return cls(
            timeout_seconds=_positive_float("HTTP_TIMEOUT_SECONDS", 15),
            max_html_bytes=_positive_integer("MAX_HTML_BYTES", 5 * 1024 * 1024),
            max_redirects=_positive_integer("MAX_REDIRECTS", 3),
            min_word_count=_positive_integer("MIN_WORD_COUNT", 80),
            user_agent=os.getenv(
                "SCRAPER_USER_AGENT",
                "ArticleScraperLab/0.1",
            ).strip(),
            allow_http=_boolean("ALLOW_HTTP", False),
            respect_robots=_boolean("RESPECT_ROBOTS", True),
            robots_fail_closed=_boolean("ROBOTS_FAIL_CLOSED", True),
            allowed_domains=domains,
            worker_count=_positive_integer("WORKER_COUNT", 3),
            domain_delay_seconds=_positive_float("DOMAIN_DELAY_SECONDS", 1.0),
            database_path=database_path,
            api_key=api_key,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
