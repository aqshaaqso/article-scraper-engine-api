from datetime import date

import pytest
from pydantic import ValidationError

from article_scraper_lab import models
from article_scraper_lab.models import (
    DateFilter,
    SearchRequest,
    requested_date_filter_from_storage,
)


@pytest.mark.parametrize(
    "dates",
    [
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


def test_optional_date_inputs_are_normalized(monkeypatch):
    monkeypatch.setattr(models, "_today_in_timezone", lambda _timezone: date(2026, 9, 10))

    day = SearchRequest(query="krakatau", day=5)
    assert (day.start_date, day.end_date) == (date(2026, 9, 5), date(2026, 9, 5))
    assert day.requested_date_filter.mode == "day"
    assert day.requested_date_filter.day == 5

    month = SearchRequest(query="krakatau", month=2)
    assert (month.start_date, month.end_date) == (date(2026, 2, 1), date(2026, 2, 28))

    current_month = SearchRequest(query="krakatau", month=9)
    assert (current_month.start_date, current_month.end_date) == (
        date(2026, 9, 1),
        date(2026, 9, 10),
    )

    year = SearchRequest(query="krakatau", year=2024)
    assert (year.start_date, year.end_date) == (date(2024, 1, 1), date(2024, 12, 31))

    current_year = SearchRequest(query="krakatau", year=2026)
    assert (current_year.start_date, current_year.end_date) == (
        date(2026, 1, 1),
        date(2026, 9, 10),
    )

    open_range = SearchRequest(query="krakatau", start_date="2025-07-01")
    assert (open_range.start_date, open_range.end_date) == (
        date(2025, 7, 1),
        date(2026, 9, 10),
    )
    assert open_range.requested_date_filter.mode == "start_date"
    assert open_range.requested_date_filter.start_date == date(2025, 7, 1)


@pytest.mark.parametrize(
    "dates",
    [
        {"day": 11},
        {"month": 10},
        {"year": 2027},
        {"start_date": "2026-09-11"},
        {"start_date": "2026-09-01", "end_date": "2026-09-11"},
    ],
)
def test_future_date_inputs_are_rejected(monkeypatch, dates):
    monkeypatch.setattr(models, "_today_in_timezone", lambda _timezone: date(2026, 9, 10))

    with pytest.raises(ValidationError):
        SearchRequest(query="krakatau", **dates)


def test_legacy_future_range_can_be_read_but_not_continued(monkeypatch):
    monkeypatch.setattr(models, "_today_in_timezone", lambda _timezone: date(2026, 9, 10))
    stored = {
        "query": "krakatau",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "timezone": "Asia/Jakarta",
    }

    body = SearchRequest.model_validate(stored, context={"from_storage": True})
    criteria = DateFilter(
        start_date=body.start_date,
        end_date=body.end_date,
        timezone=body.timezone,
    )
    requested = requested_date_filter_from_storage(stored, criteria)

    assert requested.mode == "legacy_range"
    assert body.includes_future_date is True


@pytest.mark.parametrize(
    "dates",
    [
        {"day": 1, "month": 1},
        {"month": 1, "year": 2025},
        {"day": 1, "start_date": "2025-01-01"},
        {"end_date": "2025-01-01"},
    ],
)
def test_optional_date_inputs_reject_ambiguous_combinations(dates):
    with pytest.raises(ValidationError):
        SearchRequest(query="krakatau", **dates)
