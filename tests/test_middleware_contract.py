from fastapi.testclient import TestClient

from article_scraper_lab.main import app


def test_request_id_is_returned_and_bounded() -> None:
    response = TestClient(app).get("/health", headers={"X-Request-ID": "trace-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-123"


def test_readiness_is_additive_to_v031_contract() -> None:
    client = TestClient(app)
    assert client.get("/ready").json() == {
        "status": "ready",
        "database": "postgresql",
        "schema_version": 4,
    }
    paths = app.openapi()["paths"]
    expected = {
        "/health",
        "/v1/articles/scrape",
        "/v1/jobs",
        "/v1/jobs/{job_id}",
        "/v1/search/jobs",
        "/v1/search/runs",
        "/v1/search/runs/{search_id}",
        "/v1/search/runs/{search_id}/continue",
    }
    assert expected <= paths.keys()
    schemas = app.openapi()["components"]["schemas"]
    assert "requested_date_filter" in schemas["SearchAccepted"]["properties"]
    assert "date_filter" in schemas["SearchAccepted"]["properties"]
    assert "failed" in schemas["SearchProgress"]["properties"]["status"]["enum"]
