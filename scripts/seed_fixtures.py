#!/usr/bin/env python3
"""Populate a PRIVIA database with realistic sample data.

Useful for exercising the UI without hand-creating content. Refuses to touch a
database that already has conversations, so it cannot clobber real data.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parent.parent / part)
    for part in (
        "packages/shared-types",
        "packages/security",
        "packages/storage",
        "packages/observability",
        "packages/memory",
        "packages/tool-runtime",
        "packages/agent-core",
        "services/llm",
        "services/embeddings",
        "services/speech",
        "services/integrations",
        "apps/api",
    )
]

from privia_shared.config import get_settings  # noqa: E402
from privia_shared.domain import CalendarEvent, EmailAddress  # noqa: E402
from privia_shared.enums import MemoryKind, MessageRole  # noqa: E402
from privia_shared.ids import event_id, utcnow  # noqa: E402
from privia_storage.engine import Database  # noqa: E402
from privia_storage.migrator import migrate  # noqa: E402
from privia_storage.repositories import Repositories  # noqa: E402

NOTES = [
    ("Interview preparation", "STAR method. Prepare two system design stories.", ["career"]),
    ("Reading list", "Designing Data-Intensive Applications\nThe Making of a Manager", ["books"]),
    ("Q3 retro actions", "Automate the ingest pipeline. Reduce on-call pages.", ["work"]),
]

MEMORIES = [
    ("Prefers concise answers with no preamble", MemoryKind.PREFERENCE, True),
    ("Works on an F1 analytics project in Python", MemoryKind.FACT, False),
    ("Rahul is the project manager for the analytics team", MemoryKind.FACT, False),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed PRIVIA with sample data")
    parser.add_argument("--force", action="store_true", help="Seed even if data already exists")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_directories()
    database = Database(settings.database_url)
    migrate(database)
    repositories = Repositories(database)
    repositories.users.ensure_default()

    existing = repositories.sessions.list(limit=1)
    if existing and not args.force:
        print("This database already has data. Re-run with --force to seed anyway.")
        return 1

    session_id = repositories.sessions.create("Sample conversation")
    repositories.messages.add(session_id, MessageRole.USER, "Find the project report")
    repositories.messages.add(session_id, MessageRole.ASSISTANT, "Found 1 file: project_report.md")

    for title, body, tags in NOTES:
        repositories.notes.create(title, body, tags)

    for content, kind, pinned in MEMORIES:
        repositories.memories.add(kind, content, pinned=pinned)

    repositories.drafts.create(
        [EmailAddress(address="rahul@example.com", name="Rahul")],
        "Q3 report",
        "I'll send the report tomorrow.",
    )

    import asyncio

    from privia_integrations.calendar.ics import IcsCalendarProvider

    calendar = IcsCalendarProvider(settings.calendar_dir)
    start = utcnow() + timedelta(days=1)
    asyncio.run(
        calendar.create_event(
            CalendarEvent(
                id=event_id(),
                title="Sync with Rahul",
                start=start,
                end=start + timedelta(hours=1),
                location="Zoom",
                participants=("rahul@example.com",),
            )
        )
    )

    print(f"Seeded {settings.database_url}")
    print(f"  {len(NOTES)} notes, {len(MEMORIES)} memories, 1 draft, 1 event, 1 conversation")
    database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
