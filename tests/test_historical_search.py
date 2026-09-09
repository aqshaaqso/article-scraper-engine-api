import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from article_scraper_lab import historical_search as history_module
from article_scraper_lab.article_dates import parse_article_time
from article_scraper_lab.config import get_settings
from article_scraper_lab.dependencies import get_job_manager, get_scraper_service
from article_scraper_lab.errors import ExtractionError
from article_scraper_lab.historical_search import HistoricalSearch, month_windows
from article_scraper_lab.job_manager import JobManager
from article_scraper_lab.job_store import JobStore
from article_scraper_lab.main import app
from article_scraper_lab.models import ArticleResponse, SearchRequest
from article_scraper_lab.search import fetch_news_page
from article_scraper_lab.security import UrlPolicy


def article(url, published="2024-02-29"):
    return ArticleResponse(
        source_url=url,
        final_url=url,
        canonical_url=url,
        domain="example.com",
        title="Artikel",
        content="Isi artikel",
        word_count=2,
        content_hash="hash",
        robots_status="allowed",
        fetched_at=datetime.now(UTC),
        published_at=published,
        publication_time=parse_article_time(published, "fixture"),
    )


def page(*paths, more=False):
    result = {"organic_results": [{"link": f"https://example.com/{path}"} for path in paths]}
    if more:
        result["serpapi_pagination"] = {"next": "https://evil.example/do-not-follow"}
    return result


@pytest.fixture
def setup_history(monkeypatch):
    settings = replace(
        get_settings(), serpapi_api_key="test-secret", allowed_domains=("example.com",)
    )
    policy = UrlPolicy(
        allowed_domains=settings.allowed_domains, resolver=lambda host, port: ["8.8.8.8"]
    )
    service = Mock()
    service.validate_url.side_effect = policy.validate
    service.scrape.side_effect = article
    store = JobStore(settings.database_path)
    manager = JobManager(store, service, 1)
    manager.start()
    # Deterministic worker execution with the real extraction/persistence boundary.
    monkeypatch.setattr(manager, "_schedule", lambda row: manager._process(dict(row)))
    history = HistoricalSearch(settings, service, manager)
    yield history, store, manager
    manager.shutdown()


def test_partial_months_leap_year_and_two_year_window():
    assert month_windows(date(2024, 2, 28), date(2024, 3, 2)) == [
        (date(2024, 2, 28), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 2)),
    ]
    assert len(month_windows(date(2024, 9, 9), date(2026, 9, 9))) == 25


def test_actual_provider_query_uses_custom_dates(monkeypatch):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = b'{"organic_results":[]}'
    monkeypatch.setattr("article_scraper_lab.search.build_opener", lambda *args: opener)
    fetch_news_page("secret", "keyword", 20, date(2024, 2, 1), date(2024, 2, 29))
    params = parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
    assert params["tbs"] == ["cdr:1,cd_min:02/01/2024,cd_max:02/29/2024"]
    assert params["engine"] == ["google"] and params["start"] == ["20"]


def test_provider_success_with_empty_results_is_not_a_failure(monkeypatch):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(
        {
            "search_metadata": {"status": "Success"},
            "error": "Google hasn't returned any results for this query.",
        }
    ).encode()
    monkeypatch.setattr("article_scraper_lab.search.build_opener", lambda *args: opener)
    assert fetch_news_page("secret", "keyword", 0) == {"organic_results": []}


def test_restart_recovers_queued_jobs_and_date_context(monkeypatch, setup_history):
    history, store, manager = setup_history
    monkeypatch.setattr(manager, "_schedule", lambda row: None)
    provider = Mock(side_effect=[page("a", more=True), page("a", "b")])
    monkeypatch.setattr(history_module, "fetch_news_page", provider)
    search_id = history.create(
        SearchRequest(
            query="keyword",
            start_date="2024-02-01",
            end_date="2024-02-29",
            max_pages=1,
        )
    )
    first = history.advance(search_id)
    assert store.get(first.job_id).date_report.pending == 1
    manager.shutdown()
    resumed_manager = JobManager(JobStore(history.settings.database_path), history.service, 1)
    monkeypatch.setattr(
        resumed_manager, "_schedule", lambda row: resumed_manager._process(dict(row))
    )
    try:
        resumed_manager.start()
        resumed = HistoricalSearch(history.settings, history.service, resumed_manager)
        assert resumed.progress(search_id).date_report.in_range == 1
        assert resumed.advance(search_id).urls == ["https://example.com/b"]
        assert resumed.progress(search_id).date_report.in_range == 2
    finally:
        resumed_manager.shutdown()


def test_historical_discovery_rejects_unsafe_links(monkeypatch, setup_history):
    history, _, _ = setup_history
    monkeypatch.setattr(
        history_module,
        "fetch_news_page",
        Mock(
            return_value={
                "organic_results": [
                    {"link": link}
                    for link in [
                        "https://127.0.0.1/a",
                        "https://example.com.evil.test/a",
                        "https://name:secret@example.com/a",
                        "http://example.com/a",
                        "https://example.com/tag/news",
                        "https://example.com/a#first",
                        "https://example.com/a#second",
                    ]
                ],
            }
        ),
    )
    search_id = history.create(
        SearchRequest(
            query="keyword",
            start_date="2024-02-01",
            end_date="2024-02-29",
        )
    )
    result = history.advance(search_id)
    assert result.urls == ["https://example.com/a"] and result.skipped == 6
    assert "secret" not in history.read(search_id)["state_json"]


def test_resume_mid_page_across_restart_without_duplicates(monkeypatch, setup_history):
    history, store, manager = setup_history
    provider = Mock(side_effect=[page("a", "b", "b", more=True), page("b", "c"), page("a", "d")])
    monkeypatch.setattr(history_module, "fetch_news_page", provider)
    search_id = history.create(
        SearchRequest(
            query="keyword",
            start_date="2024-02-01",
            end_date="2024-03-31",
            max_articles=1,
            max_pages=1,
        )
    )
    results = []
    for _ in range(8):
        history = HistoricalSearch(history.settings, history.service, manager)
        result = history.advance(search_id)
        results.extend(result.urls)
        if result.continue_url is None:
            break
    assert results == [f"https://example.com/{path}" for path in ("a", "b", "c", "d")]
    assert provider.call_count == 3
    assert [call.args[2] for call in provider.call_args_list] == [0, 10, 0]
    assert provider.call_args_list[0].args[3:] == (date(2024, 1, 31), date(2024, 3, 1))
    progress = history.progress(search_id)
    assert progress.status == "discovery_complete" and progress.months_completed == 2
    assert progress.discovered == 4 and progress.skipped == 3
    assert progress.date_report.in_range == 4
    assert progress.date_report.by_month == {"2024-02": 4, "2024-03": 0}
    assert all(store.get(job).search_id == search_id for job in progress.job_ids)
    count = provider.call_count
    assert history.advance(search_id).job_id is None
    assert provider.call_count == count


def test_empty_months_continue_and_report_progress(monkeypatch, setup_history):
    history, _, _ = setup_history
    monkeypatch.setattr(history_module, "fetch_news_page", Mock(return_value=page()))
    search_id = history.create(
        SearchRequest(
            query="keyword",
            start_date="2024-01-01",
            end_date="2024-12-31",
            max_pages=1,
        )
    )
    first = history.advance(search_id)
    assert first.status == "ready" and first.job_id is None and first.continue_url
    progress = history.progress(search_id)
    assert progress.months_completed == 1 and progress.current_start_date == date(2024, 2, 1)


def test_final_page_pending_rows_do_not_mark_month_complete(monkeypatch, setup_history):
    history, _, _ = setup_history
    monkeypatch.setattr(history_module, "fetch_news_page", Mock(return_value=page("a", "b")))
    search_id = history.create(
        SearchRequest(
            query="keyword",
            start_date="2024-02-01",
            end_date="2024-02-29",
            max_articles=1,
        )
    )
    history.advance(search_id)
    progress = history.progress(search_id)
    assert progress.months_completed == 0 and progress.current_start_date == date(2024, 2, 1)
    assert history.advance(search_id).continue_url is None


def test_date_report_boundaries_unknown_dates_and_failures(monkeypatch, setup_history):
    history, _, manager = setup_history
    history.service.scrape.side_effect = [
        article("https://example.com/a", "2024-02-28T17:00:00Z"),  # midnight WIB
        article("https://example.com/b", "2024-02-29T16:59:59Z"),  # last second WIB
        article("https://example.com/c", "2024-02-29T17:00:00Z"),  # next day WIB
        article("https://example.com/d", None),
        article("https://example.com/e", "2024-02-29"),
        ExtractionError("No content"),
    ]
    monkeypatch.setattr(
        history_module, "fetch_news_page", Mock(return_value=page("a", "b", "c", "d", "e", "f"))
    )
    search_id = history.create(
        SearchRequest(query="keyword", start_date="2024-02-29", end_date="2024-02-29")
    )
    job = manager.get(history.advance(search_id).job_id)
    assert job.succeeded == 5 and job.failed == 1
    assert job.date_report.in_range == 3
    assert job.date_report.out_of_range == 1 and job.date_report.unknown_date == 1
    assert job.date_report.failed == 1
    assert [item.included for item in job.items] == [True, True, False, False, True, False]
    assert job.items[0].date_basis == "report_timezone"
    assert job.items[4].date_basis == "source_date"


def test_provider_failure_keeps_checkpoint_and_no_partial_job(monkeypatch, setup_history):
    history, store, _ = setup_history
    provider = Mock(side_effect=[page("a", more=True), HTTPException(502, "Provider failed")])
    monkeypatch.setattr(history_module, "fetch_news_page", provider)
    search_id = history.create(
        SearchRequest(query="keyword", start_date="2024-02-01", end_date="2024-02-29")
    )
    with pytest.raises(HTTPException) as exc:
        history.advance(search_id)
    assert exc.value.detail["search_id"] == search_id
    assert store.recent() == []
    progress = history.progress(search_id)
    assert progress.status == "ready" and progress.discovered == 0
    assert progress.requests_attempted == 2 and progress.pages_fetched == 0
    monkeypatch.setattr(history_module, "fetch_news_page", Mock(return_value=page("a")))
    assert history.advance(search_id).total == 1


def test_atomic_rollback_on_checkpoint_error(setup_history):
    history, store, manager = setup_history

    def broken_checkpoint(db, job_id):
        raise RuntimeError("checkpoint failed")

    with pytest.raises(RuntimeError):
        manager.commit_search_batch(["https://example.com/a"], {}, broken_checkpoint)
    assert store.recent() == []
    history.service.scrape.assert_not_called()


def test_concurrent_continue_and_expired_lease(monkeypatch, setup_history):
    history, _, _ = setup_history
    provider = Mock(return_value=page())
    monkeypatch.setattr(history_module, "fetch_news_page", provider)
    search_id = history.create(
        SearchRequest(query="keyword", start_date="2024-02-01", end_date="2024-02-29")
    )
    with history.connect() as db:
        db.execute(
            "UPDATE search_runs SET lease_until=?,lease_token='other' WHERE id=?",
            (time.time() + 600, search_id),
        )
    with pytest.raises(HTTPException) as exc:
        history.advance(search_id)
    assert exc.value.status_code == 409
    provider.assert_not_called()
    with history.connect() as db:
        db.execute("UPDATE search_runs SET lease_until=0 WHERE id=?", (search_id,))
    assert history.advance(search_id).continue_url is None


def test_repeated_provider_page_stops_with_warning(monkeypatch, setup_history):
    history, _, _ = setup_history
    monkeypatch.setattr(history_module, "fetch_news_page", Mock(return_value=page("a", more=True)))
    search_id = history.create(
        SearchRequest(query="keyword", start_date="2024-02-01", end_date="2024-02-29")
    )
    assert history.advance(search_id).total == 1
    assert history.progress(search_id).warnings


def test_legacy_database_migration_preserves_rows(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY,status TEXT,total INTEGER,
            completed INTEGER DEFAULT 0,succeeded INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,
            created_at TEXT,started_at TEXT,finished_at TEXT)""")
        db.execute(
            "INSERT INTO jobs(id,status,total,created_at) VALUES ('old','completed',0,?)",
            (datetime.now(UTC).isoformat(),),
        )
    store = JobStore(path)
    store.initialize()
    store.initialize()
    assert store.get("old").date_filter is None


def test_swagger_flow_and_history_auth(monkeypatch, setup_history):
    history, _, manager = setup_history
    app.dependency_overrides[get_settings] = lambda: replace(history.settings, api_key="test-key")
    app.dependency_overrides[get_scraper_service] = lambda: history.service
    app.dependency_overrides[get_job_manager] = lambda: manager
    monkeypatch.setattr(history_module, "fetch_news_page", Mock(return_value=page("a")))
    try:
        client = TestClient(app)
        headers = {"X-API-Key": "test-key"}
        assert client.get("/v1/search/runs").status_code == 401
        response = client.post(
            "/v1/search/jobs",
            headers=headers,
            json={
                "query": "keyword",
                "start_date": "2024-02-01",
                "end_date": "2024-02-29",
            },
        )
        assert response.status_code == 202
        result = response.json()
        assert client.get(result["progress_url"]).status_code == 401
        assert (
            client.get(result["progress_url"], headers=headers).json()["date_report"]["in_range"]
            == 1
        )
        job = client.get(result["result_url"], headers=headers).json()
        assert job["items"][0]["included"] is True
        assert client.post(f"/v1/search/runs/{result['search_id']}/continue").status_code == 401
        schema = client.get("/openapi.json").json()
        assert "start_date" in schema["components"]["schemas"]["SearchRequest"]["properties"]
        assert "secret" not in json.dumps(client.get("/v1/search/runs", headers=headers).json())
    finally:
        app.dependency_overrides.clear()
