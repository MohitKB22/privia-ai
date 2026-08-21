"""``privia-migrate`` - database migration command line."""

from __future__ import annotations

import argparse
import sys

from privia_shared.config import get_settings

from .engine import Database
from .migrator import current_version, discover, migrate, reset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="privia-migrate", description="PRIVIA database migrations"
    )
    parser.add_argument(
        "command",
        choices=["up", "status", "reset"],
        nargs="?",
        default="up",
        help="up: apply pending migrations; status: show current version; reset: drop everything",
    )
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation for reset")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_directories()
    url = args.database_url or settings.database_url
    db = Database(url)

    if args.command == "status":
        version = current_version(db)
        available = discover()
        print(f"database: {url}")
        print(f"applied version: {version:04d}")
        print(f"available migrations: {len(available)}")
        pending = [m for m in available if m.version > version]
        if pending:
            print("pending:")
            for m in pending:
                print(f"  {m.version:04d}_{m.name}")
        else:
            print("pending: none")
        return 0

    if args.command == "reset":
        if not args.yes:
            answer = input(f"Drop every table in {url}? Type 'yes' to continue: ")
            if answer.strip().lower() != "yes":
                print("aborted")
                return 1
        reset(db)
        print("database reset")

    applied = migrate(db)
    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("database already up to date")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
