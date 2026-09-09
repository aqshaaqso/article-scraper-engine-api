"""Keep tests independent of local credentials and the user's article database."""

import pytest

from article_scraper_lab.config import get_settings
from article_scraper_lab.dependencies import (
    get_job_manager,
    get_rate_limiter,
    get_scraper_service,
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    values = {
        "SCRAPER_API_KEY": "",
        "SERPAPI_API_KEY": "",
        "REQUIRE_API_KEY": "false",
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "ALLOWED_DOMAINS": "",
        "ALLOW_HTTP": "false",
        "RESPECT_ROBOTS": "true",
        "ROBOTS_FAIL_CLOSED": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    caches = (get_settings, get_scraper_service, get_job_manager, get_rate_limiter)
    for factory in caches:
        factory.cache_clear()
    yield
    for factory in caches:
        factory.cache_clear()
