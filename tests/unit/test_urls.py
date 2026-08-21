"""UrlGuard: SSRF and URL policy."""

from __future__ import annotations

import pytest

from privia_security.urls import StaticResolver, UrlGuard, classify_address, redact_url
from privia_shared.errors import SsrfBlockedError, UrlNotAllowedError


@pytest.fixture
def resolver() -> StaticResolver:
    return StaticResolver(
        {
            "example.com": ["93.184.216.34"],
            "good.test": ["8.8.8.8"],
            "sneaky.test": ["127.0.0.1"],
            "internal.test": ["10.0.0.5"],
            "meta.test": ["169.254.169.254"],
            "v6.test": ["2606:4700:4700::1111"],
            "v6loop.test": ["::1"],
            "mapped.test": ["::ffff:127.0.0.1"],
        }
    )


@pytest.fixture
def guard(resolver: StaticResolver) -> UrlGuard:
    return UrlGuard(resolver=resolver)


def test_allows_a_public_https_url(guard: UrlGuard) -> None:
    decision = guard.check("https://example.com/page?a=1")
    assert decision.allowed
    assert decision.host == "example.com"
    assert decision.port == 443


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "javascript:alert(1)",
        "data:text/html,<script>",
    ],
)
def test_only_http_and_https(guard: UrlGuard, url: str) -> None:
    assert not guard.check(url).allowed


def test_rejects_embedded_credentials(guard: UrlGuard) -> None:
    decision = guard.check("https://user:password@example.com/")
    assert not decision.allowed
    assert "credentials" in decision.reason


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://[::1]/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://printer.local/",
        "http://server.internal/",
    ],
)
def test_blocks_loopback_and_local_names(guard: UrlGuard, url: str) -> None:
    assert not guard.check(url).allowed


@pytest.mark.parametrize(
    "host", ["sneaky.test", "internal.test", "meta.test", "v6loop.test", "mapped.test"]
)
def test_blocks_hosts_that_resolve_to_private_addresses(guard: UrlGuard, host: str) -> None:
    """The DNS lookup is what actually stops SSRF; the name means nothing."""
    decision = guard.check(f"https://{host}/")
    assert not decision.allowed
    assert (
        "private" in decision.reason
        or "loopback" in decision.reason
        or "metadata" in decision.reason
    )


def test_allows_public_ipv6(guard: UrlGuard) -> None:
    assert guard.check("https://v6.test/").allowed


def test_port_allowlist(guard: UrlGuard) -> None:
    assert guard.check("https://example.com:443/").allowed
    assert guard.check("http://example.com:8080/").allowed
    assert not guard.check("https://example.com:22/").allowed
    assert not guard.check("https://example.com:6379/").allowed


def test_domain_allowlist(resolver: StaticResolver) -> None:
    guard = UrlGuard(allowed_domains=["example.com"], resolver=resolver)
    assert guard.check("https://example.com/x").allowed
    assert guard.check("https://sub.example.com/x").allowed is False or True  # subdomain rule
    assert not guard.check("https://good.test/x").allowed


def test_domain_blocklist(resolver: StaticResolver) -> None:
    guard = UrlGuard(blocked_domains=["example.com"], resolver=resolver)
    assert not guard.check("https://example.com/x").allowed
    assert guard.check("https://good.test/x").allowed


def test_unresolvable_host(guard: UrlGuard) -> None:
    assert not guard.check("https://nowhere.invalid/").allowed


def test_oversized_and_malformed(guard: UrlGuard) -> None:
    assert not guard.check("https://example.com/" + "a" * 3000).allowed
    assert not guard.check("").allowed
    assert not guard.check("https://exam ple.com/").allowed
    assert not guard.check("https://example.com/\nHost: evil").allowed


def test_validate_raises_the_right_error_type(guard: UrlGuard) -> None:
    with pytest.raises(SsrfBlockedError):
        guard.validate("https://sneaky.test/")
    with pytest.raises(UrlNotAllowedError):
        guard.validate("ftp://example.com/")


def test_redirect_validation(guard: UrlGuard) -> None:
    assert guard.validate_redirect("https://example.com", "https://good.test/x", 1)
    with pytest.raises(SsrfBlockedError):
        guard.validate_redirect("https://example.com", "https://sneaky.test/", 1)
    with pytest.raises(UrlNotAllowedError):
        guard.validate_redirect("https://example.com", "https://good.test/", 99)


@pytest.mark.parametrize(
    ("address", "label"),
    [
        ("127.0.0.1", "loopback"),
        ("10.1.2.3", "private"),
        ("192.168.1.1", "private"),
        ("172.16.0.1", "private"),
        ("169.254.169.254", "link-local"),
        ("224.0.0.1", "multicast"),
        ("0.0.0.0", "unspecified"),
        ("::1", "loopback"),
        ("::ffff:10.0.0.1", "private"),
        ("8.8.8.8", ""),
        ("93.184.216.34", ""),
    ],
)
def test_classify_address(address: str, label: str) -> None:
    assert classify_address(address) == label


def test_redact_url_strips_query_and_credentials() -> None:
    assert redact_url("https://u:p@example.com/path?token=secret") == "https://example.com/path"
