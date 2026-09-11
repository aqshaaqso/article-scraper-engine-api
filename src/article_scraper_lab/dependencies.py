"""FastAPI dependency wiring for the PostgreSQL-backed Go engine."""

from functools import lru_cache
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from .config import Settings, get_settings
from .postgres_gateway import PostgresGateway

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@lru_cache
def get_postgres_gateway() -> PostgresGateway:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL wajib diisi untuk middleware Go/PostgreSQL")
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
PostgresGatewayDep = Annotated[PostgresGateway, Depends(get_postgres_gateway)]
ApiKeyDep = Annotated[None, Depends(require_api_key)]
