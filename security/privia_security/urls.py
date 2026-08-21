"""URL validation and SSRF protection.

The browser tool is the only component that talks to arbitrary hosts, so this
module is the boundary between "the model asked for a URL" and "a socket is
opened". It enforces:

* scheme allowlist (``http``/``https`` only)
* no embedded credentials (``https://user:pass@host``)
* port allowlist
* optional domain allowlist / blocklist
* DNS resolution followed by a private-address check on **every** resolved IP,
  which is what actually stops SSRF (``http://localhost`` is trivial to spell
  in a dozen ways, but every one of them resolves to a loopback address)
* the same check re-applied to each redirect hop
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from privia_shared.errors import SsrfBlockedError, UrlNotAllowedError

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5

#: Host suffixes that are meaningful only on a local network.
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localdomain", ".home.arpa", ".lan")
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)
#: Cloud instance metadata endpoints. Blocked by IP too, but named for clarity.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})


@dataclass(frozen=True)
class UrlDecision:
    url: str
    host: str
    port: int
    scheme: str
    allowed: bool
    reason: str = ""
    resolved_ips: tuple[str, ...] = ()

    def raise_for_status(self) -> str:
        if not self.allowed:
            error = (
                SsrfBlockedError
                if "private" in self.reason or "loopback" in self.reason
                else UrlNotAllowedError
            )
            raise error(self.reason, details={"url": self.url, "host": self.host})
        return self.url


class UrlGuard:
    """Validates URLs before any request is made."""

    def __init__(
        self,
        *,
        allowed_domains: Iterable[str] = (),
        blocked_domains: Iterable[str] = (),
        allow_private_addresses: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        self.allowed_domains = tuple(d.lower().lstrip(".") for d in allowed_domains if d)
        self.blocked_domains = tuple(d.lower().lstrip(".") for d in blocked_domains if d)
        self.allow_private_addresses = allow_private_addresses
        self.resolver: Resolver = resolver or SystemResolver()

    # -- public API ----------------------------------------------------------

    def check(self, raw_url: str) -> UrlDecision:
        url = (raw_url or "").strip()
        if not url:
            return UrlDecision(url, "", 0, "", False, "The URL is empty.")
        if len(url) > MAX_URL_LENGTH:
            return UrlDecision(url, "", 0, "", False, "The URL is too long.")
        if any(ch in url for ch in ("\n", "\r", "\x00", " ")):
            return UrlDecision(url, "", 0, "", False, "The URL contains illegal characters.")

        try:
            parsed = urlparse(url)
        except ValueError as exc:
            return UrlDecision(url, "", 0, "", False, f"The URL could not be parsed: {exc}")

        scheme = (parsed.scheme or "").lower()
        if scheme not in ALLOWED_SCHEMES:
            return UrlDecision(
                url,
                parsed.hostname or "",
                0,
                scheme,
                False,
                f"Only http and https are supported (got '{scheme or 'none'}').",
            )
        if parsed.username or parsed.password:
            return UrlDecision(
                url,
                parsed.hostname or "",
                0,
                scheme,
                False,
                "URLs with embedded credentials are refused.",
            )

        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return UrlDecision(url, "", 0, scheme, False, "The URL has no host.")

        try:
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError:
            return UrlDecision(url, host, 0, scheme, False, "The URL has an invalid port.")
        if port not in ALLOWED_PORTS:
            return UrlDecision(
                url, host, port, scheme, False, f"Port {port} is not on the allowed port list."
            )

        if host in BLOCKED_HOSTNAMES or host in METADATA_ADDRESSES:
            return UrlDecision(
                url, host, port, scheme, False, "That host is a loopback or metadata endpoint."
            )
        if any(host.endswith(suffix) for suffix in BLOCKED_HOST_SUFFIXES):
            return UrlDecision(
                url, host, port, scheme, False, "Local network hostnames are blocked."
            )

        if self.blocked_domains and _domain_matches(host, self.blocked_domains):
            return UrlDecision(url, host, port, scheme, False, "That domain is on your blocklist.")
        if self.allowed_domains and not _domain_matches(host, self.allowed_domains):
            return UrlDecision(
                url,
                host,
                port,
                scheme,
                False,
                "That domain is not on your allowlist. Add it in Settings to visit it.",
            )

        ips = self._resolve(host)
        if ips is None:
            return UrlDecision(url, host, port, scheme, False, "That host could not be resolved.")
        if not self.allow_private_addresses:
            for ip in ips:
                verdict = classify_address(ip)
                if verdict:
                    return UrlDecision(
                        url,
                        host,
                        port,
                        scheme,
                        False,
                        f"'{host}' resolves to a {verdict} address ({ip}); "
                        "requests to private or loopback networks are blocked.",
                        tuple(ips),
                    )

        normalised = urlunparse(
            (scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, "")
        )
        return UrlDecision(normalised, host, port, scheme, True, "", tuple(ips))

    def validate(self, raw_url: str) -> str:
        return self.check(raw_url).raise_for_status()

    def validate_redirect(self, from_url: str, to_url: str, hop: int) -> str:
        """Re-validate each redirect target; a redirect is a fresh request."""
        if hop > MAX_REDIRECTS:
            raise UrlNotAllowedError(
                f"Too many redirects (>{MAX_REDIRECTS}).", details={"url": from_url}
            )
        return self.validate(to_url)

    def _resolve(self, host: str) -> list[str] | None:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return [str(literal)]
        try:
            return self.resolver.resolve(host)
        except OSError:
            return None


class Resolver:
    """Indirection so tests can resolve without touching DNS."""

    def resolve(self, host: str) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError


class SystemResolver(Resolver):
    def resolve(self, host: str) -> list[str]:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return sorted({str(info[4][0]) for info in infos})


class StaticResolver(Resolver):
    """Test double: a fixed host -> addresses map."""

    def __init__(self, mapping: dict[str, Sequence[str]]) -> None:
        self.mapping = {k.lower(): list(v) for k, v in mapping.items()}

    def resolve(self, host: str) -> list[str]:
        try:
            return self.mapping[host.lower()]
        except KeyError as exc:
            raise OSError(f"unknown host {host}") from exc


def classify_address(raw_ip: str) -> str:
    """Return a non-empty label when the address must not be contacted."""
    try:
        ip = ipaddress.ip_address(raw_ip)
    except ValueError:
        return "unparseable"
    # Unwrap IPv4-in-IPv6 first. ``::ffff:10.0.0.1`` is a private address wearing
    # an IPv6 costume; classifying the wrapper would report "reserved" and hide
    # what is actually being contacted.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return classify_address(str(ip.ipv4_mapped)) or "mapped"
        if ip.sixtofour is not None:
            return classify_address(str(ip.sixtofour)) or "6to4"
        if ip.teredo is not None:
            return classify_address(str(ip.teredo[1])) or "teredo"

    # Order matters only for the label, not for the verdict: every branch here
    # blocks. Narrower categories are checked first so the message is precise
    # (0.0.0.0 is both "unspecified" and, per ipaddress, "private").
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"
    if str(ip) in METADATA_ADDRESSES:
        return "metadata"
    return ""


def _domain_matches(host: str, patterns: Sequence[str]) -> bool:
    return any(host == pattern or host.endswith("." + pattern) for pattern in patterns)


def redact_url(url: str) -> str:
    """Strip query strings and credentials before a URL is logged."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<unparseable url>"
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
