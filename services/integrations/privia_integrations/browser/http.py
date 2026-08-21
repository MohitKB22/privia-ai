"""HTTP            title, text, links = browser adapter.

This is a *reader*, not a browser automation surface. It performs GET requests
to validated public URLs, follows redirects manually so every hop is re-checked,
caps the response size, and returns text marked as untrusted.

It never submits forms, never sends cookies or credentials, and never executes
JavaScript.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx

from privia_security.injection import scan
from privia_security.urls import MAX_REDIRECTS, UrlGuard
from privia_shared.domain import IntegrationInfo, PageContent, SearchResult
from privia_shared.errors import IntegrationUnavailableError, ToolError, UrlNotAllowedError

from ..base import BrowserProvider
from .extract import extract_text, strip_tags

USER_AGENT = "PRIVIA/1.0 (local personal assistant; +https://github.com/privia-app/privia)"
#: Content types we will read as text.
READABLE_TYPES = (
    "text/html",
    "text/plain",
    "application/xhtml",
    "application/json",
    "text/markdown",
)


class HttpBrowserProvider(BrowserProvider):
    name = "http"
    display_name = "Web reader"

    def __init__(
        self,
        guard: UrlGuard,
        *,
        timeout_seconds: float = 15.0,
        max_bytes: int = 2 * 1024 * 1024,
        search_endpoint: str = "https://html.duckduckgo.com/html/",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.guard = guard
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.search_endpoint = search_endpoint
        self._client = client

    def capabilities(self) -> tuple[str, ...]:
        return ("search", "open_url", "extract_text", "redirect_validation", "ssrf_protection")

    async def health_check(self) -> IntegrationInfo:
        allowed = self.guard.allowed_domains
        detail = (
            f"restricted to {len(allowed)} domain(s)"
            if allowed
            else "public web, private IPs blocked"
        )
        return self.ok(detail)

    # -- client ---------------------------------------------------------------

    def _make_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(self.timeout_seconds),
            # trust_env=False is a security control, not a convenience.
            # httpx would otherwise honour HTTP_PROXY / ALL_PROXY from the
            # environment, and a proxy re-resolves the hostname itself. That
            # would completely bypass UrlGuard: we would validate the IP we
            # resolved, then hand the *name* to a proxy that connects wherever
            # it likes. It also silently breaks on a machine with a SOCKS proxy
            # configured but no socksio installed.
            trust_env=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": "en",
            },
            max_redirects=0,
        )

    # -- operations -----------------------------------------------------------

    async def open_url(self, url: str, *, max_chars: int = 20_000) -> PageContent:
        validated = self.guard.validate(url)
        client = self._make_client()
        owns_client = self._client is None
        try:
            final_url, response = await self._get_following_redirects(client, validated)
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and not any(content_type.startswith(t) for t in READABLE_TYPES):
                raise ToolError(
                    f"'{content_type}' is not readable as text. PRIVIA only reads web pages.",
                    details={"url": final_url, "content_type": content_type},
                )
            body = response.content[: self.max_bytes]
            truncated_bytes = len(response.content) > self.max_bytes
            encoding = response.encoding or "utf-8"
            try:
                html = body.decode(encoding, errors="replace")
            except LookupError:
                html = body.decode("utf-8", errors="replace")

            title: str
            text: str
            links: list[str]
            if content_type.startswith(("text/plain", "application/json")):
                # Plain text and JSON are returned verbatim: there is no markup
                # to strip and no links to collect.
                title, text, links = "", html, []
            else:
                title, text, links = extract_text(html, final_url)

            report = scan(text)
            clipped = report.sanitized_text[:max_chars]
            return PageContent(
                url=validated,
                final_url=final_url,
                title=title,
                text=clipped,
                untrusted=True,
                truncated=truncated_bytes or len(report.sanitized_text) > max_chars,
                bytes_fetched=len(body),
                content_type=content_type,
                links=tuple(links[:40]),
                injection_flags=tuple(report.flags),
            )
        except httpx.TimeoutException as exc:
            raise IntegrationUnavailableError(
                f"The page took longer than {self.timeout_seconds:.0f}s to respond.",
                details={"url": validated},
            ) from exc
        except httpx.HTTPError as exc:
            raise IntegrationUnavailableError(
                f"The page could not be fetched: {type(exc).__name__}",
                details={"url": validated},
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def _get_following_redirects(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[str, httpx.Response]:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            response = await client.get(current)
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise UrlNotAllowedError(
                        "The server sent a redirect without a destination.",
                        details={"url": current},
                    )
                target = httpx.URL(current).join(location)
                current = self.guard.validate_redirect(current, str(target), hop + 1)
                await response.aclose()
                continue
            response.raise_for_status()
            return current, response
        raise UrlNotAllowedError(f"Too many redirects (>{MAX_REDIRECTS}).", details={"url": url})

    async def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        cleaned = re.sub(r"\s+", " ", query).strip()
        if not cleaned:
            return []
        endpoint = f"{self.search_endpoint}?q={quote_plus(cleaned[:300])}"
        client = self._make_client()
        owns_client = self._client is None
        try:
            response = await client.post(
                self.search_endpoint,
                data={"q": cleaned[:300]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.is_redirect:
                _final, response = await self._get_following_redirects(client, endpoint)
            response.raise_for_status()
            return self._parse_results(response.text, limit)
        except httpx.HTTPError as exc:
            raise IntegrationUnavailableError(
                "Web search is unavailable right now. PRIVIA works offline; only web lookups "
                "need a connection.",
                details={"reason": type(exc).__name__},
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _parse_results(html: str, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        blocks = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
            r"(.*?)(?=<a[^>]+class=\"[^\"]*result__a|\Z)",
            html,
            re.DOTALL | re.IGNORECASE,
        )
        from html import unescape
        from urllib.parse import parse_qs, urlparse

        for raw_url, raw_title, tail in blocks:
            url = unescape(raw_url)
            if url.startswith("//duckduckgo.com/l/") or "uddg=" in url:
                query = parse_qs(urlparse(url if url.startswith("http") else f"https:{url}").query)
                url = query.get("uddg", [url])[0]
            if not url.startswith("http"):
                continue
            title = re.sub(r"\s+", " ", unescape(strip_tags(raw_title))).strip()
            snippet_match = re.search(
                r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', tail, re.DOTALL | re.IGNORECASE
            )
            snippet = ""
            if snippet_match:
                snippet = re.sub(r"\s+", " ", unescape(strip_tags(snippet_match.group(1)))).strip()
            results.append(SearchResult(title=title[:200], url=url, snippet=snippet[:400]))
            if len(results) >= limit:
                break
        return results


class MockBrowserProvider(BrowserProvider):
    """Deterministic offline browser used by tests and by ``--offline`` mode."""

    name = "mock"
    display_name = "Offline web reader (mock)"

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}

    def capabilities(self) -> tuple[str, ...]:
        return ("search", "open_url", "extract_text")

    async def health_check(self) -> IntegrationInfo:
        return self.ok(f"{len(self.pages)} canned page(s)")

    async def open_url(self, url: str, *, max_chars: int = 20_000) -> PageContent:
        html = self.pages.get(url)
        if html is None:
            raise IntegrationUnavailableError(
                "Offline mode: that page is not in the local cache.", details={"url": url}
            )
        title, text, links = extract_text(html, url)
        report = scan(text)
        return PageContent(
            url=url,
            final_url=url,
            title=title,
            text=report.sanitized_text[:max_chars],
            untrusted=True,
            bytes_fetched=len(html),
            content_type="text/html",
            links=tuple(links),
            injection_flags=tuple(report.flags),
        )

    async def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        return [
            SearchResult(title=f"Offline result for {query}", url=url, snippet="cached page")
            for url in list(self.pages)[:limit]
        ]
