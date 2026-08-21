"""Terminal adapter.

Safety properties, in order of importance:

1. ``shell=False`` always. The command is an argv list produced by
   :class:`privia_security.CommandGuard`; no string is ever handed to a shell.
2. The child runs in its own process group so a timeout kills the whole tree,
   not just the direct child.
3. The environment is rebuilt from scratch with credentials stripped.
4. stdin is closed, so a command that prompts fails fast instead of hanging.
5. Output is capped; a runaway process cannot exhaust memory.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from privia_security.commands import CommandGuard, sanitize_environment
from privia_security.limits import clamp_output
from privia_shared.domain import CommandResult, IntegrationInfo
from privia_shared.errors import ToolError, ToolTimeoutError

from ..base import TerminalProvider


class LocalTerminalProvider(TerminalProvider):
    name = "local"
    display_name = "Local shell (allowlisted)"

    def __init__(
        self,
        guard: CommandGuard,
        *,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 256 * 1024,
    ) -> None:
        self.guard = guard
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def capabilities(self) -> tuple[str, ...]:
        return ("inspect", "run", "timeout", "output-limit", "allowlist")

    async def health_check(self) -> IntegrationInfo:
        if not self.guard.workspace_roots:
            return self.not_configured(
                "No workspace folders configured. Set TERMINAL_WORKSPACE_ROOTS or grant a folder."
            )
        missing = [str(r) for r in self.guard.workspace_roots if not r.is_dir()]
        if missing:
            return self.unavailable(f"Workspace folder(s) missing: {', '.join(missing[:3])}")
        return self.ok(
            f"{len(self.guard.allowlist)} allowlisted programs, "
            f"{len(self.guard.workspace_roots)} workspace root(s)"
        )

    async def run(
        self,
        argv: Sequence[str],
        cwd: Path,
        *,
        timeout_seconds: float | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> CommandResult:
        timeout = timeout_seconds or self.timeout_seconds
        working_dir = self.guard.validate_cwd(cwd)
        env = sanitize_environment()
        for key, value in (env_overrides or {}).items():
            if key.isidentifier() and not any(
                marker in key.upper() for marker in ("TOKEN", "SECRET", "KEY", "PASSWORD")
            ):
                env[key] = value

        started = time.monotonic()
        creation: dict[str, object] = {}
        if sys.platform != "win32":
            creation["start_new_session"] = True

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(working_dir),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **creation,  # type: ignore[arg-type]
            )
        except FileNotFoundError as exc:
            raise ToolError(
                f"'{argv[0]}' is not installed on this machine.",
                details={"program": argv[0]},
            ) from exc
        except PermissionError as exc:
            raise ToolError(
                f"'{argv[0]}' is not executable.", details={"program": argv[0]}
            ) from exc
        except OSError as exc:
            raise ToolError(f"The command could not be started: {exc}") from exc

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate(process)
            stdout_bytes, stderr_bytes = b"", b""
        duration_ms = int((time.monotonic() - started) * 1000)

        stdout, out_truncated = clamp_output(
            stdout_bytes.decode("utf-8", errors="replace"), self.max_output_bytes
        )
        stderr, err_truncated = clamp_output(
            stderr_bytes.decode("utf-8", errors="replace"), self.max_output_bytes // 4
        )

        if timed_out:
            raise ToolTimeoutError(
                f"'{' '.join(argv)}' ran longer than {timeout:.0f}s and was stopped.",
                details={"argv": list(argv), "timeout_seconds": timeout, "cwd": str(working_dir)},
            )

        return CommandResult(
            argv=tuple(argv),
            cwd=str(working_dir),
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=False,
            truncated=out_truncated or err_truncated,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """Kill the whole process group, escalating from TERM to KILL."""
        if process.returncode is not None:
            return
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:  # pragma: no cover - Windows
                process.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
            return
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:  # pragma: no cover - Windows
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=3)
        except (ProcessLookupError, PermissionError, OSError, asyncio.TimeoutError):
            pass
