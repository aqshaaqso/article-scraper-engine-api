"""FastAPI dependency wiring."""

from functools import lru_cache
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from .config import Settings, get_settings
from .postgres_gateway import PostgresGateway

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@lru_cache
def get_rate_limiter():
    from .rate_limiter import DomainRateLimiter

    return DomainRateLimiter(get_settings().domain_delay_seconds)


@lru_cache
def get_scraper_service():
    settings = get_settings()
    if settings.database_url:
        return get_postgres_gateway()
    from .extractor import ArticleExtractor
    from .http_client import SecureHttpClient
    from .robots import RobotsChecker
    from .security import UrlPolicy
    from .service import ArticleScraperService
    policy = UrlPolicy(
        allow_http=settings.allow_http,
        allowed_domains=settings.allowed_domains,
    )
    client = SecureHttpClient(
        policy,
        timeout_seconds=settings.timeout_seconds,
        max_bytes=settings.max_html_bytes,
        max_redirects=settings.max_redirects,
        user_agent=settings.user_agent,
    )
    robots = RobotsChecker(
        client,
        user_agent=settings.user_agent,
        enabled=settings.respect_robots,
        fail_closed=settings.robots_fail_closed,
    )
    return ArticleScraperService(
        policy=policy,
        client=client,
        robots=robots,
        extractor=ArticleExtractor(settings.min_word_count),
        limiter=get_rate_limiter(),
    )


@lru_cache
def get_job_manager():
    settings = get_settings()
    if settings.database_url:
        return get_postgres_gateway()
    from .job_manager import JobManager
    from .job_store import JobStore
    return JobManager(
        store=JobStore(settings.database_path),
        service=get_scraper_service(),
        worker_count=settings.worker_count,
    )


@lru_cache
def get_postgres_gateway() -> PostgresGateway:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL wajib diisi untuk mode middleware")
    return PostgresGateway(
        settings.database_url,
        worker_count=settings.worker_count,
        sync_timeout=settings.sync_scrape_timeout_seconds,
    )


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    provided_key: Annotated[str | None, Security(api_key_header)],
) -> None:
    if settings.api_key is None:
        return
    if provided_key is None or not compare_digest(provided_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key tidak valid",
        )


SettingsDep = Annotated[Settings, Depends(get_settings)]
ScraperServiceDep = Annotated[object, Depends(get_scraper_service)]
JobManagerDep = Annotated[object, Depends(get_job_manager)]
ApiKeyDep = Annotated[None, Depends(require_api_key)]
