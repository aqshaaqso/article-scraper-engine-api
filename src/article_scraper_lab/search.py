"""Bounded SerpAPI news discovery followed by the existing scraper queue."""

import json
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException

from .config import Settings
from .errors import UnsafeUrlError
from .job_manager import JobManager
from .models import SearchAccepted, SearchRequest
from .service import ArticleScraperService


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_news_page(
    api_key: str,
    query: str,
    start: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    # Fixed provider host. Never follow provider-supplied pagination URLs or redirects.
    parameters = {
        "engine": "google",
        "q": query,
        "gl": "id",
        "hl": "id",
        "start": start,
        "api_key": api_key,
    }
    if start_date is not None and end_date is not None:
        parameters["tbs"] = f"cdr:1,cd_min:{start_date:%m/%d/%Y},cd_max:{end_date:%m/%d/%Y}"
    params = urlencode(parameters)
    request = Request("https://serpapi.com/search.json?" + params)
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("response too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
    except HTTPError as error:
        detail = {
            401: "SERPAPI_API_KEY tidak valid.",
            403: "Akses SerpAPI ditolak; periksa key dan akun.",
            429: "Kuota atau rate limit SerpAPI tercapai.",
        }.get(error.code, "Pencarian SerpAPI gagal. Coba kembali nanti.")
        raise HTTPException(502, detail) from None
    except (URLError, TimeoutError, OSError, ValueError):
        # Provider exception strings can contain the URL with the API key.
        raise HTTPException(502, "SerpAPI tidak dapat diakses atau respons tidak valid.") from None
    if payload.get("error") == "Google hasn't returned any results for this query.":
        metadata = payload.get("search_metadata", {})
        if isinstance(metadata, dict) and metadata.get("status") == "Success":
            return {"organic_results": []}
    if payload.get("error"):
        raise HTTPException(502, "SerpAPI mengembalikan error; periksa akun dan kuota.")
    if not isinstance(payload.get("organic_results", []), list):
        raise HTTPException(502, "Format hasil berita SerpAPI tidak valid.")
    return payload


def search_and_submit(
    body: SearchRequest,
    settings: Settings,
    service: ArticleScraperService,
    manager: JobManager,
) -> SearchAccepted:
    if not settings.serpapi_api_key:
        raise HTTPException(503, "Isi SERPAPI_API_KEY di .env lalu buat ulang container.")
    if not settings.allowed_domains:
        raise HTTPException(503, "ALLOWED_DOMAINS wajib diisi untuk pencarian berita.")
    if body.start_date is not None:
        from .historical_search import HistoricalSearch

        history = HistoricalSearch(settings, service, manager)
        search_id = history.create(body)
        return history.advance(search_id)
    sites = " OR ".join(f"site:{domain}" for domain in settings.allowed_domains)
    query = f"{body.query} ({sites})"
    urls: list[str] = []
    seen: set[str] = set()
    skipped = 0
    pages = 0
    for page in range(body.max_pages):
        payload = fetch_news_page(settings.serpapi_api_key, query, page * 10)
        pages += 1
        rows = payload.get("organic_results", [])
        for row in rows:
            link = row.get("link") if isinstance(row, dict) else None
            if not isinstance(link, str):
                skipped += 1
                continue
            try:
                url = service.validate_url(link).url
            except UnsafeUrlError:
                skipped += 1
                continue
            path = urlsplit(url).path.lower()
            listing = path == "/" or path.startswith(
                ("/tag/", "/tags/", "/topic/", "/topik/", "/search", "/indeks", "/index")
            )
            if url in seen or listing:
                skipped += 1
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= body.max_articles:
                break
        if len(urls) >= body.max_articles or not rows:
            break
        pagination = payload.get("serpapi_pagination")
        if not isinstance(pagination, dict) or not pagination.get("next"):
            break
    # Search must finish successfully before creating a job; no hidden partial queue on error.
    job = manager.submit(urls) if urls else None
    return SearchAccepted(
        query=body.query,
        status="queued" if job else "no_results",
        job_id=job.job_id if job else None,
        total=len(urls),
        pages_fetched=pages,
        skipped=skipped,
        urls=urls,
        result_url=f"/v1/jobs/{job.job_id}" if job else None,
    )
