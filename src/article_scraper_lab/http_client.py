"""Pinned-IP HTTP client with secure manual redirect handling."""

from __future__ import annotations

import http.client
import socket
import ssl
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from urllib.parse import urljoin

from .errors import FetchError
from .security import UrlPolicy, UrlTarget

BeforeRequest = Callable[[UrlTarget], AbstractContextManager[None]]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: UrlTarget, timeout: float) -> None:
        super().__init__(target.hostname, target.port, timeout=timeout)
        self._resolved_ip = target.resolved_ips[0]

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._resolved_ip, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: UrlTarget, timeout: float) -> None:
        super().__init__(
            target.hostname,
            target.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._resolved_ip = target.resolved_ips[0]

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._resolved_ip, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    final_url: str
    redirect_chain: tuple[str, ...]

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        for part in content_type.split(";")[1:]:
            key, separator, value = part.strip().partition("=")
            if separator and key.lower() == "charset":
                charset = value.strip("\"'") or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class SecureHttpClient:
    redirect_statuses = {301, 302, 303, 307, 308}

    def __init__(
        self,
        policy: UrlPolicy,
        *,
        timeout_seconds: float,
        max_bytes: int,
        max_redirects: int,
        user_agent: str,
    ) -> None:
        self._policy = policy
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent

    def fetch(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        before_request: BeforeRequest | None = None,
    ) -> HttpResponse:
        current_url = url
        redirects: list[str] = []
        limit = max_bytes or self._max_bytes
        for redirect_count in range(self._max_redirects + 1):
            target = self._policy.validate(current_url)
            request_context = (
                before_request(target) if before_request is not None else nullcontext()
            )
            with request_context:
                response = self._request_once(target, limit)
            location = response.headers.get("location")
            if response.status_code not in self.redirect_statuses:
                return HttpResponse(
                    status_code=response.status_code,
                    headers=response.headers,
                    body=response.body,
                    final_url=target.url,
                    redirect_chain=tuple(redirects),
                )
            if not location:
                raise FetchError("Provider mengirim redirect tanpa Location")
            if redirect_count >= self._max_redirects:
                raise FetchError("Jumlah redirect melewati batas")
            redirects.append(target.url)
            current_url = urljoin(target.url, location)
        raise FetchError("Jumlah redirect melewati batas")

    def _request_once(self, target: UrlTarget, max_bytes: int) -> HttpResponse:
        connection_class = (
            _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
        )
        connection = connection_class(target, self._timeout_seconds)
        try:
            connection.request(
                "GET",
                target.request_target,
                headers={
                    "Host": target.host_header,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5",
                    "Accept-Encoding": "identity",
                    "User-Agent": self._user_agent,
                    "Connection": "close",
                },
            )
            upstream = connection.getresponse()
            content_length = upstream.getheader("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    raise FetchError("Content-Length upstream tidak valid") from None
                if declared_length > max_bytes:
                    raise FetchError("Respons melebihi batas ukuran")
            body = upstream.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FetchError("Respons melebihi batas ukuran")
            headers = {key.lower(): value for key, value in upstream.getheaders()}
            return HttpResponse(
                status_code=upstream.status,
                headers=headers,
                body=body,
                final_url=target.url,
                redirect_chain=(),
            )
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise FetchError(f"Gagal mengambil halaman: {type(exc).__name__}") from None
        finally:
            connection.close()
