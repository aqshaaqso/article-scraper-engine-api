from fastapi.testclient import TestClient

from article_scraper_lab.config import get_settings
from article_scraper_lab.dependencies import get_postgres_gateway
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
    assert "url: '/openapi.json'" in swagger.text
    assert '"persistAuthorization": true' in swagger.text

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["openapi"].startswith("3.")
    assert client.get("/health").status_code == 200
    assert "/v1/articles/scrape" in app.openapi()["paths"]


def test_search_swagger_has_date_filter_examples_and_api_key_authorization() -> None:
    schema = app.openapi()
    examples = schema["paths"]["/v1/search/jobs"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]

    assert set(examples) == {"general", "day", "month", "year", "start_date", "range"}
    assert examples["general"]["value"] == {
        "query": "Indonesia",
        "max_articles": 10,
        "max_pages": 1,
    }
    assert examples["range"]["value"]["start_date"] == "2025-07-15"
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }


def test_root_redirects_to_swagger() -> None:
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/swagger/index.html"
    assert TestClient(app).get("/dashboard").status_code == 404


def test_malformed_job_and_search_ids_are_rejected_before_database_access() -> None:
    client = TestClient(app)

    assert client.get("/v1/jobs/not-a-uuid").status_code == 422
    assert client.get("/v1/search/runs/not-a-uuid").status_code == 422
    assert client.post("/v1/search/runs/not-a-uuid/continue").status_code == 422


def test_async_job_is_limited_to_one_hundred_urls() -> None:
    schema = app.openapi()["components"]["schemas"]["BatchScrapeRequest"]
    assert schema["properties"]["urls"]["maxItems"] == 100


def test_api_key_protects_job_history_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("SCRAPER_API_KEY", "test-secret")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        assert client.get("/v1/jobs").status_code == 401
        assert client.get("/v1/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/v1/jobs", headers={"X-API-Key": "test-secret"}).status_code == 200
    finally:
        get_postgres_gateway.cache_clear()
        get_settings.cache_clear()


def test_async_job_rejects_more_than_one_hundred_urls() -> None:
    response = TestClient(app).post(
        "/v1/jobs",
        json={"urls": [f"https://example.com/article-{index}" for index in range(101)]},
    )
    assert response.status_code == 422
