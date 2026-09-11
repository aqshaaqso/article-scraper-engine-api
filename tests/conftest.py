"""Keep middleware tests independent of local credentials and PostgreSQL."""

from types import SimpleNamespace

import pytest

from article_scraper_lab.config import get_settings
from article_scraper_lab.dependencies import get_postgres_gateway
from article_scraper_lab.main import app


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setenv("SCRAPER_API_KEY", "")
    monkeypatch.setenv("REQUIRE_API_KEY", "false")
    monkeypatch.setenv("DATABASE_URL", "postgres://test.invalid/article_scraper")
    gateway = SimpleNamespace(health=lambda: True, recent=lambda: [])
    get_settings.cache_clear()
    get_postgres_gateway.cache_clear()
    app.dependency_overrides[get_postgres_gateway] = lambda: gateway
    yield
    app.dependency_overrides.clear()
    get_postgres_gateway.cache_clear()
    get_settings.cache_clear()
