from fastapi.testclient import TestClient

from article_scraper_lab.config import get_settings
from article_scraper_lab.dependencies import get_job_manager, get_scraper_service
from article_scraper_lab.main import app


def test_swagger_and_health_are_available() -> None:
    client = TestClient(app)
    docs_redirect = client.get("/docs", follow_redirects=False)
    assert docs_redirect.status_code == 307
    assert docs_redirect.headers["location"] == "/swagger/index.html"

    swagger = client.get("/swagger/index.html")
    assert swagger.status_code == 200
    assert "Swagger UI" in swagger.text
    assert "SwaggerUIBundle" in swagger.text
    assert "swagger-ui-dist@5" in swagger.text
    assert 'url: \'/openapi.json\'' in swagger.text

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["openapi"].startswith("3.")
    assert client.get("/health").status_code == 200
    assert "/v1/articles/scrape" in app.openapi()["paths"]


def test_api_rejects_loopback_before_network_access() -> None:
    get_scraper_service.cache_clear()
    response = TestClient(app).post(
        "/v1/articles/scrape",
        json={"url": "https://127.0.0.1/private"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_url"


def test_root_redirects_to_swagger() -> None:
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/swagger/index.html"
    assert TestClient(app).get("/dashboard").status_code == 404


def test_async_job_rejects_unsafe_url_before_queueing() -> None:
    get_scraper_service.cache_clear()
    response = TestClient(app).post(
        "/v1/jobs",
        json={"urls": ["https://127.0.0.1/private"]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_url"


def test_async_job_is_limited_to_one_hundred_urls() -> None:
    schema = app.openapi()["components"]["schemas"]["BatchScrapeRequest"]
    assert schema["properties"]["urls"]["maxItems"] == 100


def test_api_key_protects_job_history_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("SCRAPER_API_KEY", "test-secret")
    get_settings.cache_clear()
    get_job_manager.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/v1/jobs").status_code == 401
            assert client.get("/v1/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
            assert (
                client.get("/v1/jobs", headers={"X-API-Key": "test-secret"}).status_code == 200
            )
    finally:
        get_job_manager.cache_clear()
        get_settings.cache_clear()


def test_async_job_rejects_more_than_one_hundred_urls() -> None:
    response = TestClient(app).post(
        "/v1/jobs",
        json={"urls": [f"https://example.com/article-{index}" for index in range(101)]},
    )
    assert response.status_code == 422
