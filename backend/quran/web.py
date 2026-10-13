"""Allow-listed web lookup for the Quran agent. Every rule is enforced here, not in a prompt."""

import ipaddress
import json
import logging
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from quran.types import WebPassage

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = ("api.quran.com",)
# Not verified yet, so never queried until someone moves them into ALLOWED_HOSTS.
DISABLED_HOSTS = ("quran.ksu.edu.sa", "islamweb.net")

# quran.com's public JSON API (v4). Its search matches words of the Quran text, so the question is
# reduced to its distinctive words first; the tafsirs it returns are books the local store lacks.
API = "https://api.quran.com/api/v4"
SEARCH_URL = API + "/search?size=5&q={q}"
TAFSIR_URL = API + "/tafsirs/{tafsir}/by_ayah/{verse}"
VERSE_URL = "https://quran.com/{verse}"
PAGE_URL = VERSE_URL + "/tafsirs/{slug}"
TAFSIRS = (("ar-tafsir-ibn-kathir", 14, "ابن كثير"),)
MAX_TOKENS = 4
MAX_VERSES = 2

MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 2500
TIMEOUT_SECONDS = 8.0
REDIRECT_CODES = (301, 302, 303, 307, 308)


def _host_key(host: str) -> str:
    host = host.lower().rstrip(".")
    return host.removeprefix("www.")


def is_allowed_url(url: str, hosts: tuple[str, ...] = ALLOWED_HOSTS) -> bool:
    """https only, no userinfo, no IP literal, host equal to an allowed host (or its www form)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or "@" in parts.netloc:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return _host_key(host) in hosts


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "head", "nav", "footer", "form"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:MAX_TEXT_CHARS]


class AllowListedWeb:
    def __init__(self, client: httpx.AsyncClient, hosts: tuple[str, ...] = ALLOWED_HOSTS):
        self._client = client
        self._hosts = tuple(h for h in hosts if h in ALLOWED_HOSTS)

    async def search(self, query: str) -> list[WebPassage]:
        from quran.retrieval import keyword_tokens

        query = query.strip()
        if not query or "\x00" in query:
            return []
        verses = await self._verses(keyword_tokens(query)[:MAX_TOKENS])
        passages = []
        for key, text in verses[:MAX_VERSES]:
            passages.append(WebPassage(url=VERSE_URL.format(verse=key), text=f"({key}) {text}"))
            for slug, tafsir_id, _author in TAFSIRS:
                passage = await self._tafsir(key, slug, tafsir_id)
                if passage is not None:
                    passages.append(passage)
        return passages

    async def _verses(self, tokens: list[str]) -> list[tuple[str, str]]:
        """(verse_key, text) for the verses that match the most question words, best first."""
        found: dict[str, tuple[int, str]] = {}
        for token in tokens:
            data = await self._json(SEARCH_URL.format(q=quote(token)))
            for row in ((data or {}).get("search") or {}).get("results") or []:
                key, text = row.get("verse_key"), row.get("text")
                if isinstance(key, str) and isinstance(text, str):
                    found[key] = (found.get(key, (0, text))[0] + 1, text)
        ranked = sorted(found.items(), key=lambda item: -item[1][0])  # stable: first hit wins ties
        return [(key, text) for key, (_count, text) in ranked]

    async def _tafsir(self, verse: str, slug: str, tafsir_id: int) -> WebPassage | None:
        data = await self._json(TAFSIR_URL.format(tafsir=tafsir_id, verse=quote(verse)))
        html = ((data or {}).get("tafsir") or {}).get("text")
        text = extract_text(html) if isinstance(html, str) else ""
        return WebPassage(url=PAGE_URL.format(verse=verse, slug=slug), text=text) if text else None

    async def _json(self, url: str) -> dict | None:
        try:
            body = await self._get(url)
            data = json.loads(body) if body else None
        except Exception as exc:  # any failure means no evidence from this call
            logger.info("web lookup failed: %s", type(exc).__name__)
            return None
        return data if isinstance(data, dict) else None

    async def _get(self, url: str) -> str | None:
        for _ in range(MAX_REDIRECTS + 1):
            if not is_allowed_url(url, self._hosts):
                return None
            async with self._client.stream(
                "GET", url, follow_redirects=False, timeout=TIMEOUT_SECONDS
            ) as response:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    url = urljoin(url, location)
                    continue
                return await self._read(response)
        return None

    async def _read(self, response: httpx.Response) -> str | None:
        if response.status_code != 200:
            return None
        if "json" not in response.headers.get("content-type", "").lower():
            return None
        body = b""
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                return None
        return body.decode(response.encoding or "utf-8", errors="replace")
