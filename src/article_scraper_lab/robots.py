"""robots.txt checks performed through the same secure HTTP client."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .errors import FetchError, RobotsDeniedError
from .http_client import BeforeRequest, SecureHttpClient


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    status: str
    crawl_delay: float | None = None


class RobotsChecker:
    def __init__(
        self,
        client: SecureHttpClient,
        *,
        user_agent: str,
        enabled: bool,
        fail_closed: bool,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._enabled = enabled
        self._fail_closed = fail_closed

    def assert_allowed(
        self, url: str, *, before_request: BeforeRequest | None = None
    ) -> RobotsDecision:
        if not self._enabled:
            return RobotsDecision(status="disabled")
        parsed = urlsplit(url)
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        try:
            response = self._client.fetch(
                robots_url,
                max_bytes=512 * 1024,
                before_request=before_request,
            )
        except FetchError:
            if self._fail_closed:
                raise RobotsDeniedError("robots.txt tidak dapat diperiksa") from None
            return RobotsDecision(status="unavailable")
        if response.status_code == 404:
            return RobotsDecision(status="not_found")
        if response.status_code in {401, 403}:
            raise RobotsDeniedError("robots.txt menolak akses crawler")
        if not 200 <= response.status_code < 300:
            if self._fail_closed:
                raise RobotsDeniedError(f"robots.txt mengembalikan HTTP {response.status_code}")
            return RobotsDecision(status="unavailable")

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text().splitlines())
        if not parser.can_fetch(self._user_agent, url):
            raise RobotsDeniedError("URL tidak diizinkan oleh robots.txt")
        delay = parser.crawl_delay(self._user_agent) or parser.crawl_delay("*")
        return RobotsDecision(status="allowed", crawl_delay=delay)
