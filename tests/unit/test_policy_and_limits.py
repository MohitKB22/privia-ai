"""Permission engine, rate limiting and output limits."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from privia_security.limits import RateLimiter, clamp_output, enforce_payload_size
from privia_security.policy import PermissionEngine, describe_scope
from privia_shared.enums import PermissionDecision, RiskLevel, Scope
from privia_shared.errors import PayloadTooLargeError, RateLimitedError, ToolOutputTooLargeError
from privia_shared.permissions import PolicyRequest


def request(scope: Scope, *, risk: RiskLevel = RiskLevel.LOW, resources=()) -> PolicyRequest:
    return PolicyRequest(
        session_id="ses_1",
        tool_name="test.tool",
        scopes=(scope,),
        risk_level=risk,
        resources=tuple(resources),
    )


def test_default_is_prompt_not_allow() -> None:
    """An ungranted scope must never fall through to allow."""
    engine = PermissionEngine()
    assert engine.evaluate(request(Scope.FILES_READ)).decision is PermissionDecision.PROMPT


def test_granted_scope_allows() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.FILES_READ)
    assert engine.evaluate(request(Scope.FILES_READ)).allowed


def test_explicit_denial_is_sticky() -> None:
    engine = PermissionEngine()
    engine.deny(Scope.EMAIL_SEND)
    result = engine.evaluate(request(Scope.EMAIL_SEND))
    assert result.decision is PermissionDecision.DENY
    assert "previously denied" in result.reason


def test_resource_narrowing_for_paths(workspace: Path, outside_dir: Path) -> None:
    engine = PermissionEngine()
    engine.grant(Scope.FILES_READ, resources=[str(workspace)])
    inside = request(Scope.FILES_READ, resources=[str(workspace / "project_report.md")])
    outside = request(Scope.FILES_READ, resources=[str(outside_dir / "secret.txt")])
    assert engine.evaluate(inside).allowed
    assert engine.evaluate(outside).decision is PermissionDecision.PROMPT


def test_resource_narrowing_for_domains() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.BROWSER_READ, resources=["example.com"])
    assert engine.evaluate(request(Scope.BROWSER_READ, resources=["example.com"])).allowed
    assert engine.evaluate(request(Scope.BROWSER_READ, resources=["sub.example.com"])).allowed
    assert not engine.evaluate(request(Scope.BROWSER_READ, resources=["evil.test"])).allowed


def test_resource_narrowing_for_programs() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.TERMINAL_EXEC, resources=["pytest", "git"])
    assert engine.evaluate(request(Scope.TERMINAL_EXEC, resources=["pytest"])).allowed
    assert not engine.evaluate(request(Scope.TERMINAL_EXEC, resources=["rm"])).allowed


def test_unnarrowed_grant_covers_everything_the_guards_permit(workspace: Path) -> None:
    engine = PermissionEngine()
    engine.grant(Scope.FILES_READ)
    assert engine.evaluate(request(Scope.FILES_READ, resources=[str(workspace / "x")])).allowed


def test_expiry() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.FILES_READ, ttl_seconds=60)
    assert engine.evaluate(request(Scope.FILES_READ)).allowed
    from datetime import timedelta

    from privia_shared.ids import utcnow

    future = utcnow() + timedelta(seconds=120)
    assert (
        engine.evaluate(request(Scope.FILES_READ), now=future).decision is PermissionDecision.PROMPT
    )


def test_missing_scopes_are_reported() -> None:
    engine = PermissionEngine()
    result = engine.evaluate(
        PolicyRequest(
            session_id="s",
            tool_name="email.reply",
            scopes=(Scope.EMAIL_READ, Scope.EMAIL_DRAFT),
            risk_level=RiskLevel.LOW,
        )
    )
    assert set(result.missing_scopes) == {Scope.EMAIL_READ, Scope.EMAIL_DRAFT}


def test_high_risk_always_requires_confirmation() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.TERMINAL_EXEC)
    result = engine.evaluate(request(Scope.TERMINAL_EXEC, risk=RiskLevel.HIGH))
    assert result.allowed
    assert result.requires_confirmation


def test_low_risk_does_not_require_confirmation() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.FILES_READ)
    assert not engine.evaluate(request(Scope.FILES_READ)).requires_confirmation


def test_remembered_confirmation_applies_per_tool() -> None:
    engine = PermissionEngine()
    engine.grant(Scope.TERMINAL_EXEC)
    engine.remember_confirmation("test.tool")
    assert not engine.evaluate(
        request(Scope.TERMINAL_EXEC, risk=RiskLevel.HIGH)
    ).requires_confirmation
    engine.forget_confirmations()
    assert engine.evaluate(request(Scope.TERMINAL_EXEC, risk=RiskLevel.HIGH)).requires_confirmation


def test_every_scope_has_a_plain_language_description() -> None:
    for scope in Scope:
        description = describe_scope(scope)
        assert description != scope.value, f"{scope} has no description"
        assert len(description) > 10


def test_rate_limiter_blocks_after_the_limit() -> None:
    limiter = RateLimiter(3, window_seconds=60)
    for _ in range(3):
        limiter.check("k")
    with pytest.raises(RateLimitedError) as caught:
        limiter.check("k")
    assert caught.value.details["retry_after_seconds"] >= 0


def test_rate_limiter_is_keyed() -> None:
    limiter = RateLimiter(1)
    limiter.check("a")
    limiter.check("b")
    with pytest.raises(RateLimitedError):
        limiter.check("a")


def test_rate_limiter_window_expires() -> None:
    limiter = RateLimiter(1, window_seconds=0.05)
    limiter.check("k")
    time.sleep(0.08)
    limiter.check("k")


def test_rate_limiter_remaining_and_reset() -> None:
    limiter = RateLimiter(5)
    limiter.check("k", cost=2)
    assert limiter.remaining("k") == 3
    limiter.reset("k")
    assert limiter.remaining("k") == 5


def test_clamp_output_truncates() -> None:
    text, truncated = clamp_output("x" * 1000, 100)
    assert truncated
    assert "[output truncated at limit]" in text
    assert len(text.encode()) < 200


def test_clamp_output_leaves_small_values_alone() -> None:
    text, truncated = clamp_output("small", 100)
    assert text == "small"
    assert not truncated


def test_clamp_output_hard_mode_raises() -> None:
    with pytest.raises(ToolOutputTooLargeError):
        clamp_output("x" * 1000, 100, hard=True)


def test_enforce_payload_size() -> None:
    enforce_payload_size(10, 100)
    with pytest.raises(PayloadTooLargeError):
        enforce_payload_size(1000, 100, "upload")
