from dataclasses import replace
from unittest.mock import MagicMock, Mock
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from article_scraper_lab import search
from article_scraper_lab.config import get_settings
from article_scraper_lab.main import app
from article_scraper_lab.models import JobAccepted, SearchRequest
from article_scraper_lab.security import UrlPolicy


@pytest.mark.parametrize("code", [301, 401, 403, 429, 500])
def test_provider_errors_do_not_expose_key(monkeypatch, code):
    opener = Mock()
    opener.open.side_effect = HTTPError(
        "https://serpapi.com/?api_key=secret", code, "secret", {}, None
    )
    monkeypatch.setattr(search, "build_opener", lambda *args: opener)
    with pytest.raises(HTTPException) as error:
        search.fetch_news_page("secret", "krakatau", 0)
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail


def test_provider_contract_and_response_size(monkeypatch):
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = b'{"organic_results": [{"link":"https://detik.com/a"}]}'
    monkeypatch.setattr(search, "build_opener", lambda *args: opener)
    assert len(search.fetch_news_page("secret", "krakatau", 10)["organic_results"]) == 1
    url = opener.open.call_args.args[0].full_url
    assert url.startswith("https://serpapi.com/search.json?")
    assert "tbm=" not in url and "start=10" in url and "gl=id" in url
    for raw in [
        b"not json",
        b"[]",
        b'{"error":"secret"}',
        b'{"organic_results":{}}',
        b"x" * (2 * 1024 * 1024 + 1),
    ]:
        response.read.return_value = raw
        with pytest.raises(HTTPException):
            search.fetch_news_page("secret", "krakatau", 0)


@pytest.fixture
def setup_search():
    settings = replace(get_settings(), serpapi_api_key="secret", allowed_domains=("detik.com",))
    policy = UrlPolicy(
        allowed_domains=settings.allowed_domains, resolver=lambda host, port: ["8.8.8.8"]
    )
    service = Mock()
    service.validate_url.side_effect = policy.validate
    manager = Mock()
    manager.submit.return_value = JobAccepted(
        job_id="job1", status="queued", total=2, worker_count=3
    )
    return settings, service, manager


def test_search_filters_deduplicates_and_paginates(monkeypatch, setup_search):
    settings, service, manager = setup_search
    provider = Mock(
        side_effect=[
            {
                "organic_results": [
                    {"link": "https://news.detik.com/article#one"},
                    {"link": "https://news.detik.com/article#two"},
                    {"link": "https://detik.com.evil.example/article"},
                    {"link": "https://127.0.0.1/secret"},
                    {"link": "https://user:password@detik.com/article"},
                    {"link": "http://detik.com/article"},
                    {"link": "https://detik.com:8443/article"},
                    {"title": "no url"},
                ],
                "serpapi_pagination": {"next": "https://evil.example"},
            },
            {"organic_results": [{"link": "https://detik.com/second"}]},
        ]
    )
    monkeypatch.setattr(search, "fetch_news_page", provider)
    result = search.search_and_submit(
        SearchRequest(query="krakatau", max_pages=2), settings, service, manager
    )
    assert result.total == 2 and result.skipped == 7 and result.pages_fetched == 2
    manager.submit.assert_called_once_with(
        ["https://news.detik.com/article", "https://detik.com/second"]
    )
    assert [call.args[2] for call in provider.call_args_list] == [0, 10]
    assert "site:detik.com" in provider.call_args.args[1]


def test_no_results_and_page_limit(monkeypatch, setup_search):
    settings, service, manager = setup_search
    provider = Mock(return_value={"organic_results": [], "serpapi_pagination": {"next": "x"}})
    monkeypatch.setattr(search, "fetch_news_page", provider)
    result = search.search_and_submit(SearchRequest(query="krakatau"), settings, service, manager)
    assert result.status == "no_results" and result.job_id is None
    manager.submit.assert_not_called()
    provider.assert_called_once()


def test_limit_stops_additional_provider_requests(monkeypatch, setup_search):
    settings, service, manager = setup_search
    provider = Mock(
        return_value={
            "organic_results": [{"link": "https://detik.com/a"}, {"link": "https://detik.com/b"}],
            "serpapi_pagination": {"next": "x"},
        }
    )
    monkeypatch.setattr(search, "fetch_news_page", provider)
    result = search.search_and_submit(
        SearchRequest(query="krakatau", max_articles=1, max_pages=5), settings, service, manager
    )
    assert result.total == 1
    provider.assert_called_once()


def test_provider_failure_does_not_queue_partial_job(monkeypatch, setup_search):
    settings, service, manager = setup_search
    provider = Mock(
        side_effect=[
            {
                "organic_results": [{"link": "https://detik.com/a"}],
                "serpapi_pagination": {"next": "x"},
            },
            HTTPException(502),
        ]
    )
    monkeypatch.setattr(search, "fetch_news_page", provider)
    with pytest.raises(HTTPException):
        search.search_and_submit(
            SearchRequest(query="krakatau", max_pages=2), settings, service, manager
        )
    manager.submit.assert_not_called()


@pytest.mark.parametrize("change", [{"serpapi_api_key": None}, {"allowed_domains": ()}])
def test_missing_configuration_fails_closed(change, setup_search):
    settings, service, manager = setup_search
    with pytest.raises(HTTPException) as error:
        search.search_and_submit(
            SearchRequest(query="krakatau"), replace(settings, **change), service, manager
        )
    assert error.value.status_code == 503
    manager.submit.assert_not_called()


def test_search_endpoint_auth_and_input_validation(monkeypatch):
    settings = replace(get_settings(), api_key="testing", serpapi_api_key=None)
    app.dependency_overrides[get_settings] = lambda: settings
    provider = Mock()
    monkeypatch.setattr(search, "fetch_news_page", provider)
    try:
        client = TestClient(app)
        assert client.post("/v1/search/jobs", json={"query": "krakatau"}).status_code == 401
        headers = {"X-API-Key": "testing"}
        for body in [
            {"query": "   "},
            {"query": "krakatau", "max_pages": 6},
            {"query": "krakatau", "max_articles": 51},
        ]:
            assert client.post("/v1/search/jobs", headers=headers, json=body).status_code == 422
        assert (
            client.post("/v1/search/jobs", headers=headers, json={"query": "krakatau"}).status_code
            == 503
        )
        provider.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_search_pipeline_persists_success_and_failure(monkeypatch, tmp_path, setup_search):
    import time
    from datetime import UTC, datetime

    from article_scraper_lab.errors import ExtractionError
    from article_scraper_lab.job_manager import JobManager
    from article_scraper_lab.job_store import JobStore
    from article_scraper_lab.models import ArticleResponse

    settings, service, _ = setup_search
    article = ArticleResponse(
        source_url="https://detik.com/a",
        final_url="https://detik.com/a",
        canonical_url="https://detik.com/a",
        domain="detik.com",
        title="Krakatau",
        content="Isi artikel",
        word_count=2,
        content_hash="test",
        robots_status="allowed",
        fetched_at=datetime.now(UTC),
    )
    service.scrape.side_effect = [article, ExtractionError("Tidak ada isi artikel")]
    provider = Mock(
        return_value={
            "organic_results": [
                {"link": "https://detik.com/tag/krakatau"},
                {"link": "https://detik.com/"},
                {"link": "https://detik.com/a"},
                {"link": "https://detik.com/b"},
            ]
        }
    )
    monkeypatch.setattr(search, "fetch_news_page", provider)
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, service, 1)
    manager.start()
    try:
        accepted = search.search_and_submit(
            SearchRequest(query="krakatau"), settings, service, manager
        )
        assert accepted.skipped == 2 and accepted.total == 2
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = manager.get(accepted.job_id)
            if result.completed == 2:
                break
            time.sleep(0.01)
        assert result.status == "completed"
        assert result.succeeded == 1 and result.failed == 1
        assert result.items[0].article.title == "Krakatau"
        assert result.items[1].error_code == "extraction_failed"
        assert JobStore(tmp_path / "jobs.db").get(accepted.job_id).completed == 2
    finally:
        manager.shutdown()
