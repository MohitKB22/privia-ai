"""``privia-doctor`` - diagnose a PRIVIA installation.

Answers, in order: is the configuration valid, does the database work, is a
model reachable, are the integrations healthy, and what is currently permitted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from privia_shared.config import get_settings
from privia_shared.errors import ConfigurationError

OK = "  ok   "
WARN = " warn  "
FAIL = " fail  "


def _line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f"  -  {detail}" if detail else ""))


async def _run(verbose: bool) -> int:
    problems = 0
    warnings = 0

    print("PRIVIA doctor\n" + "=" * 60)

    settings = get_settings()
    try:
        config_warnings = settings.validate_startup()
        _line(OK, "configuration", f"env={settings.app_env}")
        for warning in config_warnings:
            _line(WARN, "configuration", warning)
            warnings += 1
    except ConfigurationError as exc:
        for problem in exc.details.get("problems", []):
            _line(FAIL, "configuration", problem)
        return 2

    settings.ensure_directories()
    _line(OK, "data directory", str(settings.data_dir))

    from privia_storage.engine import Database
    from privia_storage.migrator import current_version, migrate

    try:
        database = Database(settings.database_url)
        applied = migrate(database)
        _line(
            OK,
            "database",
            f"schema v{current_version(database):04d}"
            + (f", applied {len(applied)}" if applied else ""),
        )
    except Exception as exc:
        _line(FAIL, "database", f"{type(exc).__name__}: {exc}")
        return 2

    from .container import build_container

    container = build_container(settings, configure_logs=False)
    await container.startup()

    local = await container.router.local_health()
    if local.available:
        _line(OK, "local model", f"{local.provider}:{local.model} ({local.latency_ms} ms)")
    else:
        _line(WARN, "local model", local.detail)
        warnings += 1
        _line(OK, "fallback", "offline rule engine is available; PRIVIA still works")

    cloud = await container.router.cloud_health()
    if cloud is None:
        _line(OK, "cloud model", "not configured (local-only, which is the default)")
    elif cloud.available:
        _line(WARN, "cloud model", f"{cloud.provider} reachable - data can leave the device")
        warnings += 1
    else:
        _line(OK, "cloud model", cloud.detail)

    embed = await container.embedder.health_check()
    _line(OK if embed.available else WARN, "embeddings", embed.detail)

    for info in await container.providers.health():
        status = {
            "ready": OK,
            "not_configured": WARN,
            "auth_required": WARN,
            "unavailable": WARN,
            "error": FAIL,
        }.get(info.status.value, WARN)
        if status == FAIL:
            problems += 1
        elif status == WARN:
            warnings += 1
        _line(status, f"integration {info.name}", info.detail)

    stt = await container.stt.health_check()
    _line(OK if stt.status.value == "ready" else WARN, "speech to text", stt.detail)
    tts = await container.tts.health_check()
    _line(OK if tts.status.value == "ready" else WARN, "text to speech", tts.detail)

    roots = container.providers.path_guard.roots
    if roots:
        _line(OK, "allowed folders", ", ".join(str(r) for r in roots))
    else:
        _line(WARN, "allowed folders", "none yet - file tools will refuse every path")
        warnings += 1

    granted = [
        g.scope.value for g in container.permissions.all_grants() if g.state.value == "granted"
    ]
    _line(OK, "permissions", ", ".join(granted) if granted else "nothing granted yet")

    secrets = container.secrets.describe()
    _line(
        OK, "secret store", f"{secrets['writable_backend']}, {len(secrets['stored_keys'])} stored"
    )

    _line(OK, "tools", f"{len(container.registry)} registered")

    if verbose:
        print("\nconfiguration (redacted):")
        for key, value in sorted(settings.redacted().items()):
            print(f"    {key} = {value}")

    await container.shutdown()

    print("=" * 60)
    if problems:
        print(f"{problems} problem(s), {warnings} warning(s).")
        return 1
    if warnings:
        print(f"No problems. {warnings} warning(s) - PRIVIA will run.")
        return 0
    print("Everything checks out.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="privia-doctor")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args.verbose))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
