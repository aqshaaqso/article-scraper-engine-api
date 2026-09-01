from __future__ import annotations

import pytest

from article_scraper_lab.errors import UnsafeUrlError
from article_scraper_lab.security.url_policy import UrlPolicy


def public_resolver(_hostname: str, _port: int) -> set[str]:
    return {"93.184.216.34"}


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/article",
        "https://[::1]/article",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/article",
        "https://192.168.1.1/article",
        "https://user:pass@example.com/article",
    ],
)
def test_private_and_credential_urls_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        UrlPolicy(resolver=public_resolver).validate(url)


def test_http_is_blocked_by_default() -> None:
    with pytest.raises(UnsafeUrlError, match="HTTPS"):
        UrlPolicy(resolver=public_resolver).validate("http://example.com/article")


def test_hostname_resolving_to_private_ip_is_blocked() -> None:
    policy = UrlPolicy(resolver=lambda _hostname, _port: {"10.0.0.5"})
    with pytest.raises(UnsafeUrlError, match="jaringan"):
        policy.validate("https://news.example/article")


def test_mixed_public_and_private_dns_answers_are_blocked() -> None:
    policy = UrlPolicy(resolver=lambda _hostname, _port: {"93.184.216.34", "127.0.0.1"})
    with pytest.raises(UnsafeUrlError, match="jaringan"):
        policy.validate("https://news.example/article")


def test_domain_allowlist_accepts_subdomain_and_rejects_other_domain() -> None:
    policy = UrlPolicy(allowed_domains=("example.com",), resolver=public_resolver)
    target = policy.validate("https://news.example.com/article?x=1#fragment")
    assert target.url == "https://news.example.com/article?x=1"
    with pytest.raises(UnsafeUrlError, match="ALLOWED_DOMAINS"):
        policy.validate("https://other.test/article")
