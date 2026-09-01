"""Article metadata and main-text extraction."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin, urlsplit

import trafilatura
from bs4 import BeautifulSoup

from .errors import ExtractionError
from .models import ArticleResponse


class ArticleExtractor:
    def __init__(self, min_word_count: int) -> None:
        self._min_word_count = min_word_count

    def extract(
        self,
        *,
        html: str,
        source_url: str,
        final_url: str,
        robots_status: str,
    ) -> ArticleResponse:
        soup = BeautifulSoup(html, "html.parser")
        structured = self._news_article_json_ld(soup)
        extracted = self._trafilatura_payload(html, final_url)

        content = self._clean_content(extracted.get("text") or structured.get("articleBody") or "")
        word_count = len(re.findall(r"\b\w+\b", content, flags=re.UNICODE))
        if word_count < self._min_word_count:
            raise ExtractionError(
                f"Isi artikel terlalu pendek: {word_count} kata; minimal {self._min_word_count}"
            )

        title = self._first_text(
            structured.get("headline"),
            extracted.get("title"),
            self._meta(soup, "property", "og:title"),
            soup.title.string if soup.title and soup.title.string else None,
        )
        if not title:
            raise ExtractionError("Judul artikel tidak ditemukan")

        canonical = self._canonical_url(soup, structured, final_url)
        author = self._author(structured.get("author")) or self._first_text(
            extracted.get("author"),
            self._meta(soup, "name", "author"),
        )
        publisher = structured.get("publisher")
        publisher_name = publisher.get("name") if isinstance(publisher, dict) else None
        fetched_at = datetime.now(UTC)
        return ArticleResponse(
            source_url=source_url,
            final_url=final_url,
            canonical_url=canonical,
            domain=urlsplit(canonical).hostname or urlsplit(final_url).hostname or "",
            title=title,
            author=author,
            published_at=self._first_text(structured.get("datePublished"), extracted.get("date")),
            modified_at=self._first_text(structured.get("dateModified")),
            source=self._first_text(
                publisher_name,
                extracted.get("sitename"),
                self._meta(soup, "property", "og:site_name"),
            ),
            section=self._section(structured.get("articleSection")),
            description=self._first_text(
                structured.get("description"),
                extracted.get("description"),
                self._meta(soup, "property", "og:description"),
            ),
            image_url=self._image_url(structured, soup, final_url),
            content=content,
            word_count=word_count,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            robots_status=robots_status,
            fetched_at=fetched_at,
        )

    @staticmethod
    def _trafilatura_payload(html: str, url: str) -> dict[str, Any]:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if not result:
            return {}
        try:
            decoded = json.loads(result)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def _news_article_json_ld(cls, soup: BeautifulSoup) -> dict[str, Any]:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            if not script.string:
                continue
            try:
                payload = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            for candidate in cls._json_objects(payload):
                value_type = candidate.get("@type")
                types = value_type if isinstance(value_type, list) else [value_type]
                article_types = {"NewsArticle", "Article", "ReportageNewsArticle"}
                if any(item in article_types for item in types):
                    return candidate
        return {}

    @classmethod
    def _json_objects(cls, value: Any):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from cls._json_objects(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._json_objects(item)

    @staticmethod
    def _meta(soup: BeautifulSoup, attribute: str, value: str) -> str | None:
        tag = soup.find("meta", attrs={attribute: value})
        content = tag.get("content") if tag else None
        return str(content).strip() if content else None

    @staticmethod
    def _clean_content(value: str) -> str:
        paragraphs = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
        return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        return None

    @classmethod
    def _author(cls, value: Any) -> str | None:
        if isinstance(value, list):
            names = [cls._author(item) for item in value]
            return ", ".join(name for name in names if name) or None
        if isinstance(value, dict):
            return cls._first_text(value.get("name"))
        return cls._first_text(value)

    @classmethod
    def _canonical_url(cls, soup: BeautifulSoup, structured: dict[str, Any], final_url: str) -> str:
        canonical_tag = soup.find("link", attrs={"rel": "canonical"})
        canonical_value = canonical_tag.get("href") if canonical_tag else None
        main_entity = structured.get("mainEntityOfPage")
        if isinstance(main_entity, dict):
            main_entity = main_entity.get("@id")
        candidate = cls._first_text(canonical_value, main_entity, structured.get("url"), final_url)
        return urljoin(final_url, candidate or final_url)

    @classmethod
    def _image_url(
        cls, structured: dict[str, Any], soup: BeautifulSoup, final_url: str
    ) -> str | None:
        image = structured.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        candidate = cls._first_text(image, cls._meta(soup, "property", "og:image"))
        return urljoin(final_url, candidate) if candidate else None

    @classmethod
    def _section(cls, value: Any) -> str | None:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if str(item).strip()) or None
        return cls._first_text(value)
