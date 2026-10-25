import asyncio
import hashlib
import json
import re
from collections import deque
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from app.config import Settings
from app.connectors.base import DocumentRecord, SourceRecord


class WikiError(Exception):
    """Безопасный код ошибки без содержимого ответа или секретов."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class WikiPage(DocumentRecord):
    linked_titles: list[str] = Field(default_factory=list)


class WikiBatch(BaseModel):
    pages: list[WikiPage] = Field(max_length=500)
    errors: list[dict[str, str]] = Field(default_factory=list)
    scope: dict[str, Any]
    has_more: bool = False


async def collect_wiki(settings: Settings) -> WikiBatch:
    """Коллектор может работать в сети, где доступен источник, отдельно от БД."""
    queue = deque((title, 0) for title in settings.wiki_seeds)
    titles, ids, pages, errors = set(), set(), [], []
    attempts = 0
    async with WikiConnector(settings) as connector:
        while queue and len(pages) < settings.wiki_max_pages:
            title, depth = queue.popleft()
            if title in titles:
                continue
            titles.add(title)
            attempts += 1
            if attempts > settings.wiki_max_pages * 3:
                break
            try:
                page = await connector.fetch_page(title)
                if page.external_id in ids:
                    continue
                pages.append(page)
                ids.add(page.external_id)
                if depth < settings.wiki_max_depth:
                    queue.extend((item, depth + 1) for item in page.linked_titles)
            except WikiError as exc:
                errors.append({"title": title, "code": exc.code})
            await asyncio.sleep(settings.wiki_request_delay)
    return WikiBatch(
        pages=pages,
        errors=errors,
        has_more=bool(queue),
        scope={
            "seeds": settings.wiki_seeds,
            "max_pages": settings.wiki_max_pages,
            "max_depth": settings.wiki_max_depth,
        },
    )


def clean_html(html: str) -> tuple[str, list[tuple[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select("script,style,noscript,.mw-editsection,.toc,.navbox"):
        item.decompose()
    links = [(a.get("href", ""), a.get_text(" ", strip=True)) for a in soup.select("a[href]")]
    for item in soup.select("p,li,tr,h1,h2,h3,h4,h5,h6,br"):
        item.append("\n")
    text = soup.get_text(" ", strip=False)
    text = "\n".join(
        line for raw in text.splitlines() if (line := re.sub(r"\s+", " ", raw).strip())
    )
    return text, links


class WikiConnector:
    source_id = "wiki"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=settings.wiki_request_timeout,
            follow_redirects=False,
            headers={"User-Agent": "HSEStudyAgent/0.1 (public educational content)"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def fetch(self) -> list[SourceRecord]:
        return [await self.fetch_page(title) for title in self.settings.wiki_seeds]

    async def fetch_page(self, title: str) -> WikiPage:
        params = {
            "action": "parse",
            "page": title,
            "redirects": 1,
            "prop": "text|links|revid|displaytitle",
            "format": "json",
            "formatversion": 2,
            "maxlag": 5,
        }
        payload = None
        for attempt in range(3):
            try:
                async with self.client.stream(
                    "GET", self.settings.wiki_api_url, params=params
                ) as response:
                    if response.status_code in {429, 503} and attempt < 2:
                        await asyncio.sleep(attempt + 1)
                        continue
                    if response.status_code != 200:
                        raise WikiError("wiki_http_" + str(response.status_code))
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise WikiError("wiki_response_too_large")
                payload = json.loads(body)
            except httpx.TimeoutException:
                raise WikiError("wiki_timeout") from None
            except httpx.HTTPError:
                raise WikiError("wiki_network_error") from None
            except ValueError:
                raise WikiError("wiki_invalid_json") from None
            if not isinstance(payload, dict):
                raise WikiError("wiki_invalid_payload")
            if payload.get("error", {}).get("code") == "maxlag" and attempt < 2:
                await asyncio.sleep(attempt + 1)
                continue
            break
        if not isinstance(payload, dict) or "parse" not in payload:
            raise WikiError("wiki_api_error")
        page = payload["parse"]
        try:
            page_id = str(int(page["pageid"]))
            revision = str(int(page["revid"]))
            page_title = str(page["title"])
            html = page["text"]
            if isinstance(html, dict):
                html = html["*"]
            if not isinstance(html, str):
                raise TypeError
            content, raw_links = clean_html(html)
            if not content:
                raise WikiError("wiki_empty_content")
            known = {
                item["title"]
                for item in page.get("links", [])
                if item.get("ns") == 0 and item.get("exists") is True
            }
            ordered = []
            links = []
            base = self.settings.wiki_api_url.rsplit("/", 1)[0] + "/"
            for href, label in raw_links:
                absolute = urljoin(base, href)
                parsed = urlsplit(absolute)
                if parsed.scheme in {"http", "https"}:
                    links.append({"url": absolute, "title": label})
                candidate = unquote(parsed.path.lstrip("/")).replace("_", " ")
                if candidate in known and candidate not in ordered:
                    ordered.append(candidate)
            ordered.extend(sorted(known - set(ordered)))
            return WikiPage(
                source_id="wiki",
                external_id=page_id,
                title=page_title,
                text=content,
                raw_content=html,
                revision_key="mediawiki:" + revision,
                content_hash=hashlib.sha256(
                    json.dumps(
                        {"title": page_title, "text": content, "links": links[:2000]},
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                url=base + "index.php?" + urlencode({"curid": page_id}),
                fetched_at=datetime.now(UTC),
                content_type="text/html",
                linked_titles=ordered,
                links=links[:2000],
            )
        except (KeyError, TypeError, ValueError):
            raise WikiError("wiki_invalid_payload") from None
