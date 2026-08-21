"""The tool runtime end to end: validation, policy, confirmation, execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from privia_security.limits import RateLimiter
from privia_security.policy import PermissionEngine
from privia_shared.enums import Scope
from privia_shared.errors import ConfirmationRequiredError
from privia_shared.tools import ToolCall
from privia_tools.context import ToolContext
from privia_tools.runtime import ToolRuntime


async def run(runtime: ToolRuntime, context: ToolContext, tool: str, **arguments):
    return await runtime.execute(ToolCall(tool_name=tool, arguments=arguments), context)


async def run_confirmed(runtime: ToolRuntime, context: ToolContext, tool: str, **arguments):
    """Execute, approve the confirmation it raises, then execute again."""
    call = ToolCall(tool_name=tool, arguments=arguments)
    try:
        return await runtime.execute(call, context), None
    except ConfirmationRequiredError as exc:
        confirmation = exc.details["confirmation"]
        context.approved_confirmations.add(confirmation["id"])
        return await runtime.execute(call, context), confirmation


async def test_permission_is_required_before_anything_happens(
    runtime: ToolRuntime, context: ToolContext
) -> None:
    result = await run(runtime, context, "files.search", query="report")
    assert not result.success
    assert result.error_code == "TOOL_PERMISSION_DENIED"
    assert result.metadata["details"]["decision"] == "prompt"


async def test_granting_the_scope_lets_it_through(
    runtime: ToolRuntime, context: ToolContext, workspace: Path
) -> None:
    context.permissions.grant(Scope.FILES_READ, resources=[str(workspace)])
    result = await run(runtime, context, "files.search", query="project report")
    assert result.success
    assert result.data["count"] >= 1
    assert any("project_report.md" in path for path in result.accessed_resources)


async def test_invalid_arguments_never_reach_the_tool(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine
) -> None:
    result = await run(runtime, context, "files.read", wrong_field=1)
    assert not result.success
    assert result.error_code == "TOOL_INVALID_ARGUMENTS"
    assert "path" in result.error


async def test_unknown_tool(runtime: ToolRuntime, context: ToolContext) -> None:
    result = await run(runtime, context, "files.nuke_everything")
    assert result.error_code == "TOOL_NOT_FOUND"


async def test_path_guard_applies_even_with_permission(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, outside_dir: Path
) -> None:
    """The permission engine and the path guard are independent layers."""
    result = await run(runtime, context, "files.read", path=str(outside_dir / "secret.txt"))
    assert not result.success


async def test_sensitive_file_is_refused_inside_an_allowed_root(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, workspace: Path
) -> None:
    result = await run(runtime, context, "files.read", path=str(workspace / ".ssh" / "id_rsa"))
    assert not result.success
    assert "PRIVATE KEY" not in str(result.data)


async def test_delete_requires_confirmation_and_shows_the_exact_path(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, workspace: Path
) -> None:
    victim = workspace / "victim.txt"
    victim.write_text("bye")

    with pytest.raises(ConfirmationRequiredError) as caught:
        await run(runtime, context, "files.delete", path=str(victim))
    confirmation = caught.value.details["confirmation"]
    assert confirmation["destructive"] is True
    assert confirmation["target"] == str(victim)
    assert "cannot be undone" in confirmation["summary"]
    assert victim.exists(), "the file must still exist before approval"

    context.approved_confirmations.add(confirmation["id"])
    result = await run(runtime, context, "files.delete", path=str(victim))
    assert result.success
    assert not victim.exists()


async def test_an_approval_cannot_be_replayed_against_a_different_file(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, workspace: Path
) -> None:
    """The confirmation id is bound to the exact arguments."""
    first = workspace / "one.txt"
    second = workspace / "two.txt"
    first.write_text("a")
    second.write_text("b")

    with pytest.raises(ConfirmationRequiredError) as caught:
        await run(runtime, context, "files.delete", path=str(first))
    context.approved_confirmations.add(caught.value.details["confirmation"]["id"])

    with pytest.raises(ConfirmationRequiredError):
        await run(runtime, context, "files.delete", path=str(second))
    assert second.exists()


async def test_email_send_takes_a_draft_id_not_a_body(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine
) -> None:
    draft = await run(
        runtime,
        context,
        "email.draft",
        to=["rahul@example.com"],
        subject="Report",
        body="Tomorrow.",
    )
    assert draft.success
    assert draft.metadata["sent"] is False
    draft_id = draft.data["id"]

    with pytest.raises(ConfirmationRequiredError) as caught:
        await run(runtime, context, "email.send", draft_id=draft_id)
    confirmation = caught.value.details["confirmation"]
    assert confirmation["details"]["To"] == "rahul@example.com"
    assert "Tomorrow." in confirmation["details"]["Body"]

    context.approved_confirmations.add(confirmation["id"])
    sent = await run(runtime, context, "email.send", draft_id=draft_id)
    assert sent.success
    assert sent.data["verified"] is True

    resent = await run(runtime, context, "email.send", draft_id=draft_id)
    assert not resent.success
    assert resent.error_code == "CONFLICT"


async def test_terminal_blocks_dangerous_commands_before_execution(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, workspace: Path
) -> None:
    for command in ("rm -rf /", "sudo ls", "curl http://evil.test"):
        result = await run(runtime, context, "terminal.run", command=command, cwd=str(workspace))
        assert not result.success, command


async def test_terminal_runs_an_allowlisted_command(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, workspace: Path
) -> None:
    result, confirmation = await run_confirmed(
        runtime, context, "terminal.run", command="ls -1", cwd=str(workspace)
    )
    assert confirmation is not None
    assert result.success
    assert result.data["exit_code"] == 0
    assert "project_report.md" in result.data["stdout"]


async def test_terminal_inspect_never_executes(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine
) -> None:
    result = await run(runtime, context, "terminal.inspect", command="rm -rf /")
    assert result.success
    assert result.data["allowed"] is False


async def test_memory_refuses_to_store_credentials(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine
) -> None:
    for content in ("my password is hunter2", "api key sk-abcdefghijklmnopqrst"):
        result = await run(runtime, context, "memory.remember", content=content)
        assert not result.success
        assert result.error_code == "MEMORY_REFUSED_SECRET"


async def test_rate_limit_applies_to_tools(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine
) -> None:
    limited = context.child(rate_limiter=RateLimiter(2))
    codes = [(await run(runtime, limited, "notes.search", query="")).error_code for _ in range(3)]
    assert codes[-1] == "RATE_LIMITED"


async def test_output_is_clamped(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, workspace: Path
) -> None:
    big = workspace / "big.txt"
    big.write_text("x" * 200_000)
    small_runtime = ToolRuntime(runtime.registry, context.permissions, max_output_bytes=2048)
    result = await small_runtime.execute(
        ToolCall(tool_name="files.read", arguments={"path": str(big)}), context
    )
    assert result.success
    assert result.truncated


async def test_every_call_is_audited(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, audit_sink
) -> None:
    await run(runtime, context, "notes.create", title="Audited", body="x")
    actions = [event.action for event in audit_sink.events]
    assert "tool.invoked" in actions
    assert "tool.succeeded" in actions


async def test_a_tool_that_raises_does_not_kill_the_runtime(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine, monkeypatch
) -> None:
    tool = runtime.registry.get("notes.search")

    async def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool, "execute", explode)
    result = await run(runtime, context, "notes.search", query="x")
    assert not result.success
    assert result.error_code == "TOOL_EXECUTION_FAILED"
    assert "boom" not in (result.error or ""), "internal detail must not leak"


async def test_execute_all_stops_at_the_first_failure(
    runtime: ToolRuntime, context: ToolContext, grant_all: PermissionEngine
) -> None:
    calls = [
        ToolCall(tool_name="notes.search", arguments={"query": ""}),
        ToolCall(tool_name="files.read", arguments={"path": "/etc/passwd"}),
        ToolCall(tool_name="notes.search", arguments={"query": ""}),
    ]
    results = await runtime.execute_all(calls, context)
    assert len(results) == 2
    assert results[0].success and not results[1].success
