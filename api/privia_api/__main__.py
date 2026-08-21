"""``privia-api`` entry point."""

from __future__ import annotations

import argparse
import sys

from privia_shared.config import get_settings
from privia_shared.errors import ConfigurationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="privia-api", description="Run the PRIVIA backend")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument(
        "--offline", action="store_true", help="Disable outbound network integrations"
    )
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        warnings = settings.validate_startup()
    except ConfigurationError as exc:
        print("PRIVIA cannot start:\n", file=sys.stderr)
        for problem in exc.details.get("problems", []):
            print(f"  - {problem}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    import uvicorn

    from .app import create_app

    host = args.host or settings.privia_host
    port = args.port or settings.privia_port
    print(f"PRIVIA listening on http://{host}:{port}  (docs at /docs)", file=sys.stderr)
    if args.reload:
        uvicorn.run(
            "privia_api.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_level=(args.log_level or settings.log_level).lower(),
        )
    else:
        uvicorn.run(
            create_app(settings, offline=args.offline),
            host=host,
            port=port,
            log_level=(args.log_level or settings.log_level).lower(),
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
