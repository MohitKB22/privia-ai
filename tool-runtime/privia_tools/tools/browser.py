"""Browser tools.

Everything these tools return is untrusted. The payload is wrapped by
:func:`privia_security.wrap_untrusted` before it can reach a model prompt, and
the injection score travels with the result so the UI can warn the user.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from privia_security.injection import scan, wrap_untrusted
from privia_shared.enums import AuditAction, RiskLevel, Scope
from privia_shared.tools import RetryPolicy, ToolResult

from ..context import ToolContext
from ..registry import Tool


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=6, ge=1, le=15)


class BrowserSearchTool(Tool[WebSearchArgs]):
    name = "browser.search"
    family = "browser"
    description = "Search the web and return result titles, URLs and snippets."
    scopes = (Scope.BROWSER_READ,)
    risk_level = RiskLevel.LOW
    returns_untrusted_content = True
    timeout_seconds = 30.0
    retry_policy = RetryPolicy(max_attempts=2, backoff_seconds=1.0)
    Args = WebSearchArgs

    async def execute(self, args: WebSearchArgs, ctx: ToolContext) -> ToolResult:
        results = await ctx.providers.browser.search(args.query, limit=args.limit)
        combined = " ".join(f"{r.title} {r.snippet}" for r in results)
        report = scan(combined)
        if report.suspicious:
            ctx.audit.injection_detected(
                "browser.search",
                report.flags,
                report.score,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                tool_name=self.name,
            )
        return ToolResult.ok(
            {
                "query": args.query,
                "count": len(results),
                "results": [r.model_dump(mode="json") for r in results],
                "untrusted": True,
                "injection_flags": report.flags,
            },
            accessed_resources=tuple(r.url for r in results[:10]),
            metadata={"untrusted": True, "injection_score": report.score},
        )


class OpenUrlArgs(BaseModel):
    url: str = Field(max_length=2048)
    max_chars: int = Field(default=15_000, ge=500, le=100_000)


class BrowserOpenTool(Tool[OpenUrlArgs]):
    name = "browser.open_url"
    family = "browser"
    description = (
        "Fetch a public web page and extract its readable text. Private and loopback "
        "addresses are always blocked; scripts are never executed."
    )
    scopes = (Scope.BROWSER_READ,)
    risk_level = RiskLevel.MEDIUM
    returns_untrusted_content = True
    timeout_seconds = 30.0
    retry_policy = RetryPolicy(max_attempts=2, backoff_seconds=1.0)
    Args = OpenUrlArgs

    def resources(self, args: OpenUrlArgs, ctx: ToolContext) -> tuple[str, ...]:
        decision = ctx.providers.url_guard.check(args.url)
        return (decision.host,) if decision.host else ()

    async def execute(self, args: OpenUrlArgs, ctx: ToolContext) -> ToolResult:
        page = await ctx.providers.browser.open_url(args.url, max_chars=args.max_chars)
        ctx.audit.record(
            AuditAction.URL_FETCHED,
            tool_name=self.name,
            target=page.final_url,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"bytes": page.bytes_fetched, "injection_flags": list(page.injection_flags)},
        )
        if page.injection_flags:
            ctx.audit.injection_detected(
                page.final_url,
                page.injection_flags,
                len(page.injection_flags) * 10,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                tool_name=self.name,
            )
        payload = page.model_dump(mode="json")
        payload["quarantined_text"] = wrap_untrusted(page.text, source=page.final_url)
        return ToolResult.ok(
            payload,
            accessed_resources=(page.final_url,),
            truncated=page.truncated,
            metadata={"untrusted": True, "injection_flags": list(page.injection_flags)},
        )


class InspectUrlArgs(BaseModel):
    url: str = Field(max_length=2048)


class BrowserInspectUrlTool(Tool[InspectUrlArgs]):
    name = "browser.inspect_url"
    family = "browser"
    description = "Check whether a URL would be allowed, and why, without fetching anything."
    scopes = ()
    risk_level = RiskLevel.NONE
    Args = InspectUrlArgs

    async def execute(self, args: InspectUrlArgs, ctx: ToolContext) -> ToolResult:
        decision = ctx.providers.url_guard.check(args.url)
        return ToolResult.ok(
            {
                "url": args.url,
                "allowed": decision.allowed,
                "reason": decision.reason or "The URL passes every check.",
                "host": decision.host,
                "port": decision.port,
                "scheme": decision.scheme,
                "resolved_ips": list(decision.resolved_ips),
            }
        )


BROWSER_TOOLS = [BrowserSearchTool(), BrowserOpenTool(), BrowserInspectUrlTool()]
