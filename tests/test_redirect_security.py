from __future__ import annotations

import pytest

from article_scraper_lab.errors import UnsafeUrlError
from article_scraper_lab.http_client import HttpResponse, SecureHttpClient
from article_scraper_lab.security import UrlPolicy, UrlTarget


class RedirectingClient(SecureHttpClient):
    def __init__(self, policy: UrlPolicy) -> None:
        super().__init__(
            policy,
            timeout_seconds=1,
            max_bytes=1024,
            max_redirects=3,
            user_agent="test",
        )
        self.requested: list[str] = []

    def _request_once(self, target: UrlTarget, max_bytes: int) -> HttpResponse:
        self.requested.append(target.url)
        return HttpResponse(
            status_code=302,
            headers={"location": "https://127.0.0.1/private"},
            body=b"",
            final_url=target.url,
            redirect_chain=(),
        )


def test_redirect_target_is_validated_before_second_request() -> None:
    policy = UrlPolicy(resolver=lambda _hostname, _port: {"93.184.216.34"})
    client = RedirectingClient(policy)
    with pytest.raises(UnsafeUrlError):
        client.fetch("https://public.example/article")
    assert client.requested == ["https://public.example/article"]
