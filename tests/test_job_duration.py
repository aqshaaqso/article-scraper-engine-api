from article_scraper_lab.job_store import calculate_duration_ms


def test_job_duration_uses_worker_start_and_finish_time() -> None:
    assert (
        calculate_duration_ms(
            "2026-08-31T08:00:00+00:00",
            "2026-08-31T08:00:02.345000+00:00",
        )
        == 2345
    )


def test_queued_job_has_no_duration() -> None:
    assert calculate_duration_ms(None, None) is None
