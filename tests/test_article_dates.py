from datetime import date

import pytest
from pydantic import ValidationError

from article_scraper_lab.article_dates import parse_article_time
from article_scraper_lab.extractor import ArticleExtractor
from article_scraper_lab.models import SearchRequest


@pytest.mark.parametrize(
    "raw,precision,expected,utc",
    [
        ("2024-02-29", "day", "2024-02-29", None),
        ("2024-02-29T23:45", "minute", "2024-02-29", None),
        ("2024-03-01T00:30:00+07:00", "second", "2024-03-01", "2024-02-29T17:30:00+00:00"),
        ("2024-03-01T00:30:00.123Z", "fraction", "2024-03-01", "2024-03-01T00:30:00.123000+00:00"),
        ("Kamis, 29 Februari 2024 23.45 WIB", "minute", "2024-02-29", "2024-02-29T16:45:00+00:00"),
        ("29 Februari 2024", "day", "2024-02-29", None),
        ("2023-02-29", "unknown", None, None),
        ("2024-01-01T25:00:00Z", "unknown", None, None),
        ("dua tahun lalu", "unknown", None, None),
        (None, "unknown", None, None),
    ],
)
def test_date_precision_and_missing_timezone_are_preserved(raw, precision, expected, utc):
    stamp = parse_article_time(raw, "fixture")
    assert stamp.raw == raw and stamp.source == "fixture"
    assert stamp.precision == precision
    assert (stamp.date.isoformat() if stamp.date else None) == expected
    assert (stamp.utc.isoformat() if stamp.utc else None) == utc
    if precision == "day":
        assert stamp.local_datetime is None and stamp.timezone is None
    if utc is None:
        assert stamp.timezone is None


@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": "2024-01-01"},
        {"end_date": "2024-01-01"},
        {"start_date": "2025-01-01", "end_date": "2024-01-01"},
        {"start_date": "2024-02-30", "end_date": "2025-01-01"},
        {"start_date": "2024-01-01", "end_date": "2034-01-01"},
        {"start_date": "1800-01-01", "end_date": "1800-12-31"},
        {"timezone": "unknown"},
    ],
)
def test_invalid_date_ranges(dates):
    with pytest.raises(ValidationError):
        SearchRequest(query="krakatau", **dates)


def test_metadata_fallback_is_publication_specific(monkeypatch):
    extractor = ArticleExtractor(1)
    monkeypatch.setattr(
        extractor,
        "_trafilatura_payload",
        lambda *args: {
            "text": "Artikel pengujian tanggal",
            "title": "Judul",
            "date": "2024-01-01",
        },
    )
    article = extractor.extract(
        html="""<script type="application/ld+json">
        {"@type":"NewsArticle","datePublished":"invalid","dateModified":"2026-09-09"}
        </script><meta property="article:published_time" content="2024-02-29T08:15+07:00">""",
        source_url="https://example.com/a",
        final_url="https://example.com/a",
        robots_status="allowed",
    )
    assert article.publication_time.source == "meta.article:published_time"
    assert article.publication_time.date == date(2024, 2, 29)
    assert article.modification_time.date == date(2026, 9, 9)
    assert article.published_at == "2024-02-29T08:15+07:00"
