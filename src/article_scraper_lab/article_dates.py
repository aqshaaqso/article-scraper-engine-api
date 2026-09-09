"""Conservative date parsing: never invent a clock time or source timezone."""

import re
from datetime import UTC, date, datetime, timedelta, timezone

from .models import ArticleTime, DateFilter, DateReport, JobItemResponse

REPORT_ZONES = {
    "UTC": UTC,
    "Asia/Jakarta": timezone(timedelta(hours=7)),
    "Asia/Makassar": timezone(timedelta(hours=8)),
    "Asia/Jayapura": timezone(timedelta(hours=9)),
}
MONTHS = (
    "januari",
    "februari",
    "maret",
    "april",
    "mei",
    "juni",
    "juli",
    "agustus",
    "september",
    "oktober",
    "november",
    "desember",
)


def parse_article_time(raw: str | None, source: str | None = None) -> ArticleTime:
    result = ArticleTime(raw=raw, source=source)
    if not raw:
        return result
    value = raw.strip()
    # Indonesian publisher dates, with an explicit month name (no ambiguous 01/02).
    named = re.search(
        r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})"
        r"(?:\s*(?:,|pukul)?\s*(\d{2})[:.](\d{2})(?::(\d{2}))?"
        r"(?:\s*(WIB|WITA|WIT))?)?\s*$",
        value,
        re.IGNORECASE,
    )
    if named:
        day, month, year, hour, minute, second, zone = named.groups()
        value = f"{year}-{MONTHS.index(month.lower()) + 1:02d}-{int(day):02d}"
        if hour:
            value += f"T{hour}:{minute}"
            if second:
                value += f":{second}"
            if zone:
                value += {"WIB": "+07:00", "WITA": "+08:00", "WIT": "+09:00"}[zone.upper()]
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return result.model_copy(update={"date": date.fromisoformat(value), "precision": "day"})
        match = re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?P<seconds>:\d{2})?"
            r"(?P<fraction>\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?",
            value,
        )
        if not match or (match["fraction"] and not match["seconds"]):
            return result
        parsed = datetime.fromisoformat(value.replace("z", "+00:00").replace("Z", "+00:00"))
        result.date = parsed.date()
        result.precision = (
            "fraction" if match["fraction"] else ("second" if match["seconds"] else "minute")
        )
        result.local_datetime = parsed.isoformat(
            timespec="minutes" if result.precision == "minute" else "auto"
        )
        if parsed.tzinfo is not None:
            result.utc = parsed.astimezone(UTC)
            result.timezone = parsed.strftime("%z")[:3] + ":" + parsed.strftime("%z")[3:]
    except (ValueError, OverflowError):
        return ArticleTime(raw=raw, source=source)
    return result


def empty_date_report(criteria: DateFilter) -> DateReport:
    month = criteria.start_date.replace(day=1)
    counts = {}
    while month <= criteria.end_date:
        counts[month.strftime("%Y-%m")] = 0
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return DateReport(by_month=counts)


def apply_date_filter(items: list[JobItemResponse], criteria: DateFilter) -> DateReport:
    report = empty_date_report(criteria)
    for item in items:
        if item.status in {"queued", "running"}:
            item.date_status = "pending"
            report.pending += 1
            continue
        if item.article is None:
            report.failed += 1
            item.date_status = "unknown"
            item.included = False
            continue
        stamp = item.article.publication_time
        if stamp.raw is None and item.article.published_at:
            stamp = parse_article_time(item.article.published_at, "legacy")
        published = stamp.date
        item.date_basis = "source_date" if published else None
        if stamp.utc is not None:
            published = stamp.utc.astimezone(REPORT_ZONES[criteria.timezone]).date()
            item.date_basis = "report_timezone"
        if published is None:
            item.date_status = "unknown"
            item.included = False
            report.unknown_date += 1
        elif criteria.start_date <= published <= criteria.end_date:
            item.date_status = "in_range"
            item.included = True
            report.in_range += 1
            month = published.strftime("%Y-%m")
            report.by_month[month] = report.by_month.get(month, 0) + 1
        else:
            item.date_status = "out_of_range"
            item.included = False
            report.out_of_range += 1
    return report
