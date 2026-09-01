"""Coordinates secure fetching, robots checks, and extraction."""

from __future__ import annotations

from contextlib import contextmanager

from .errors import FetchError
from .extractor import ArticleExtractor
from .http_client import SecureHttpClient
from .models import ArticleResponse
from .rate_limiter import DomainRateLimiter
from .robots import RobotsChecker, RobotsDecision
from .security import UrlPolicy, UrlTarget


class ArticleScraperService:
    def __init__(
        self,
        policy: UrlPolicy,
        client: SecureHttpClient,
        robots: RobotsChecker,
        extractor: ArticleExtractor,
        limiter: DomainRateLimiter,
    ) -> None:
        self._policy = policy
        self._client = client
        self._robots = robots
        self._extractor = extractor
        self._limiter = limiter

    def validate_url(self, raw_url: str) -> UrlTarget:
        """Validate before persistence; scrape validates again at use time."""
        return self._policy.validate(raw_url)

    def scrape(self, raw_url: str) -> ArticleResponse:
        initial_target = self._policy.validate(raw_url)
        decisions: dict[str, RobotsDecision] = {}

        def limit_request(target: UrlTarget):
            return self._limiter.limit(target.hostname)

        @contextmanager
        def check_robots_and_limit(target: UrlTarget):
            decision = self._robots.assert_allowed(
                target.url,
                before_request=limit_request,
            )
            decisions[target.hostname] = decision
            self._limiter.set_min_delay(target.hostname, decision.crawl_delay)
            with self._limiter.limit(target.hostname):
                yield

        response = self._client.fetch(initial_target.url, before_request=check_robots_and_limit)
        if not 200 <= response.status_code < 300:
            raise FetchError(f"Halaman mengembalikan HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            raise FetchError("URL tidak mengembalikan dokumen HTML")
        final_host = self._policy.validate(response.final_url).hostname
        decision = decisions.get(final_host, RobotsDecision(status="unknown"))
        return self._extractor.extract(
            html=response.text(),
            source_url=initial_target.url,
            final_url=response.final_url,
            robots_status=decision.status,
        )
