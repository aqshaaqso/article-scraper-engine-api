"""Strict URL parsing, public-IP validation, and connection targets."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from article_scraper_lab.errors import UnsafeUrlError

Resolver = Callable[[str, int], Iterable[str]]


def system_resolver(hostname: str, port: int) -> set[str]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeUrlError("Hostname tidak dapat di-resolve") from None
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses:
        raise UnsafeUrlError("Hostname tidak memiliki alamat IP")
    return addresses


@dataclass(frozen=True, slots=True)
class UrlTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    host_header: str
    request_target: str
    resolved_ips: tuple[str, ...]


class UrlPolicy:
    def __init__(
        self,
        *,
        allow_http: bool = False,
        allowed_domains: tuple[str, ...] = (),
        resolver: Resolver = system_resolver,
    ) -> None:
        self._allow_http = allow_http
        self._allowed_domains = tuple(domain.lower().rstrip(".") for domain in allowed_domains)
        self._resolver = resolver

    def validate(self, raw_url: str) -> UrlTarget:
        if not raw_url or len(raw_url) > 2048:
            raise UnsafeUrlError("URL kosong atau terlalu panjang")
        if any(ord(character) < 32 for character in raw_url):
            raise UnsafeUrlError("URL mengandung karakter kontrol")
        try:
            parsed = urlsplit(raw_url.strip())
            port = parsed.port
        except ValueError:
            raise UnsafeUrlError("Format URL tidak valid") from None
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise UnsafeUrlError("Hanya URL HTTP/HTTPS yang didukung")
        if scheme == "http" and not self._allow_http:
            raise UnsafeUrlError("HTTP tidak diizinkan; gunakan HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeUrlError("Credential tidak boleh ditanam di dalam URL")
        if not parsed.hostname:
            raise UnsafeUrlError("URL tidak memiliki hostname")

        try:
            hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            raise UnsafeUrlError("Hostname tidak valid") from None
        self._validate_domain_allowlist(hostname)

        expected_port = 443 if scheme == "https" else 80
        port = port or expected_port
        if port != expected_port:
            raise UnsafeUrlError("Hanya port standar 443/80 yang diizinkan")

        addresses = self._resolve(hostname, port)
        for address in addresses:
            self._validate_public_ip(address)

        is_ipv6 = ":" in hostname
        display_host = f"[{hostname}]" if is_ipv6 else hostname
        host_header = display_host if port == expected_port else f"{display_host}:{port}"
        path = parsed.path or "/"
        request_target = f"{path}?{parsed.query}" if parsed.query else path
        normalized_url = urlunsplit((scheme, host_header, path, parsed.query, ""))
        return UrlTarget(
            url=normalized_url,
            scheme=scheme,
            hostname=hostname,
            port=port,
            host_header=host_header,
            request_target=request_target,
            resolved_ips=tuple(sorted(addresses)),
        )

    def _resolve(self, hostname: str, port: int) -> set[str]:
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            addresses = set(self._resolver(hostname, port))
            if not addresses:
                raise UnsafeUrlError("Hostname tidak memiliki alamat IP")
            return addresses
        return {str(literal)}

    @staticmethod
    def _validate_public_ip(address: str) -> None:
        try:
            parsed = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            raise UnsafeUrlError("DNS menghasilkan alamat IP tidak valid") from None
        if not parsed.is_global:
            raise UnsafeUrlError("URL mengarah ke jaringan lokal, privat, atau khusus")

    def _validate_domain_allowlist(self, hostname: str) -> None:
        if not self._allowed_domains:
            return
        if not any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in self._allowed_domains
        ):
            raise UnsafeUrlError("Domain tidak terdaftar pada ALLOWED_DOMAINS")
