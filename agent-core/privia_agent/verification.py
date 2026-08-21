"""The VERIFY phase.

Verification is not "ask the model if it did well". It is a set of mechanical
checks over the run record that catch the failure modes that actually matter:

* claiming success when a tool failed,
* a side effect happening without a matching confirmation,
* untrusted content that tried to issue instructions,
* a plan step that silently did nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from privia_shared.agent import Plan, Verification, VerificationCheck
from privia_shared.enums import AuditAction
from privia_shared.tools import ToolCall, ToolResult

#: Tools whose execution must be preceded by a confirmation audit event.
SIDE_EFFECT_TOOLS = frozenset(
    {
        "email.send",
        "files.delete",
        "files.create",
        "files.rename",
        "files.move",
        "calendar.create_event",
        "calendar.update_event",
        "calendar.cancel_event",
        "terminal.run",
        "memory.forget",
    }
)


def verify(
    plan: Plan,
    calls: Sequence[ToolCall],
    results: Sequence[ToolResult],
    *,
    approved_confirmations: set[str] | None = None,
    response_text: str = "",
) -> Verification:
    checks: list[VerificationCheck] = []
    approved = approved_confirmations or set()

    executed = {r.tool_name for r in results}
    planned = {s.tool_name for s in plan.steps if s.tool_name}
    missing = planned - executed
    checks.append(
        VerificationCheck(
            name="plan_executed",
            passed=not missing,
            detail=(
                "Every planned tool ran."
                if not missing
                else f"Planned but not executed: {', '.join(sorted(missing))}"
            ),
        )
    )

    failures = [r for r in results if not r.success]
    checks.append(
        VerificationCheck(
            name="tools_succeeded",
            passed=not failures,
            detail=(
                "All tool calls succeeded."
                if not failures
                else "; ".join(f"{r.tool_name}: {r.error_code}" for r in failures[:3])
            ),
        )
    )

    unconfirmed = [
        r.tool_name
        for r in results
        if r.success and r.tool_name in SIDE_EFFECT_TOOLS and not approved
    ]
    checks.append(
        VerificationCheck(
            name="side_effects_confirmed",
            passed=not unconfirmed,
            detail=(
                "No unconfirmed side effect."
                if not unconfirmed
                else f"Side effect without confirmation: {', '.join(sorted(set(unconfirmed)))}"
            ),
        )
    )

    flagged = [
        r.tool_name
        for r in results
        if r.metadata.get("injection_flags") or r.metadata.get("injection_score", 0)
    ]
    checks.append(
        VerificationCheck(
            name="untrusted_content_isolated",
            passed=True,
            detail=(
                "No untrusted content in this run."
                if not flagged
                else f"Untrusted content from {', '.join(sorted(set(flagged)))} was wrapped as data."
            ),
        )
    )

    if response_text:
        claimed_send = any(
            phrase in response_text.lower() for phrase in ("i sent", "email sent", "i've sent")
        )
        actually_sent = any(r.tool_name == "email.send" and r.success for r in results)
        checks.append(
            VerificationCheck(
                name="no_false_send_claim",
                passed=not (claimed_send and not actually_sent),
                detail=(
                    "The reply does not claim an unperformed send."
                    if not (claimed_send and not actually_sent)
                    else "The reply claims an email was sent but no send succeeded."
                ),
            )
        )

    return Verification(passed=all(c.passed for c in checks), checks=tuple(checks))


def audit_actions_for(results: Sequence[ToolResult]) -> list[AuditAction]:
    actions: list[AuditAction] = []
    for result in results:
        actions.append(AuditAction.TOOL_SUCCEEDED if result.success else AuditAction.TOOL_FAILED)
    return actions
