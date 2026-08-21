"""Local calendar adapter backed by iCalendar (RFC 5545) files on disk.

One ``.ics`` file per calendar, stored under the PRIVIA data directory. This is
a real, interoperable format: the files can be opened by Apple Calendar, Google
Calendar, Thunderbird and anything else that speaks iCalendar. No cloud account
is required, and nothing leaves the machine.

Only the subset of RFC 5545 that PRIVIA needs is implemented (VEVENT with
SUMMARY/DTSTART/DTEND/LOCATION/DESCRIPTION/ATTENDEE/STATUS). Unknown properties
on existing events are preserved verbatim on rewrite.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from privia_shared.domain import CalendarEvent, IntegrationInfo
from privia_shared.errors import ConflictError, NotFoundError, ToolError
from privia_shared.ids import event_id

from ..base import CalendarProvider

_LINE_RE = re.compile(r"^(?P<name>[A-Za-z0-9-]+)(?P<params>;[^:]*)?:(?P<value>.*)$")
PRODID = "-//PRIVIA//Private Personal AI//EN"


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    if len(line) <= 75:
        return line
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(chunks)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _unescape(value: str) -> str:
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            out.append({"n": "\n", "N": "\n", ";": ";", ",": ",", "\\": "\\"}.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _to_ics_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_ics_datetime(value: str, params: str = "") -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if "VALUE=DATE" in params.upper() or len(raw) == 8:
        return datetime.strptime(raw[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ToolError(f"Unsupported date value in calendar file: {raw}") from exc


class IcsCalendarProvider(CalendarProvider):
    name = "local"
    display_name = "Local calendar (iCalendar)"

    def __init__(self, directory: Path, *, default_calendar: str = "personal") -> None:
        self.directory = Path(directory).expanduser()
        self.default_calendar = default_calendar

    def capabilities(self) -> tuple[str, ...]:
        return ("list", "search", "create", "update", "cancel", "ics-export")

    def _path(self, calendar: str) -> Path:
        name = calendar or self.default_calendar
        if name == "default":
            name = self.default_calendar
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60]
        return self.directory / f"{safe or 'personal'}.ics"

    async def health_check(self) -> IntegrationInfo:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self.errored(f"The calendar folder is not writable: {exc}")
        try:
            files = sorted(self.directory.glob("*.ics"))
            total = 0
            for path in files:
                total += len(await asyncio.to_thread(self._read_file, path))
        except Exception as exc:
            return self.errored(f"The calendar files could not be read: {exc}")
        if not files:
            return self.ok("no events yet; a calendar file is created on first use")
        return self.ok(f"{len(files)} calendar file(s), {total} event(s)")

    # -- file IO --------------------------------------------------------------

    def _read_file(self, path: Path) -> list[CalendarEvent]:
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"The calendar file could not be read: {exc}") from exc
        unfolded = re.sub(r"\r?\n[ \t]", "", raw)
        events: list[CalendarEvent] = []
        current: dict[str, tuple[str, str]] | None = None
        attendees: list[str] = []
        calendar_name = path.stem
        for line in unfolded.splitlines():
            stripped = line.strip()
            if stripped == "BEGIN:VEVENT":
                current, attendees = {}, []
                continue
            if stripped == "END:VEVENT":
                if current is not None:
                    event = self._to_event(current, attendees, calendar_name)
                    if event is not None:
                        events.append(event)
                current, attendees = None, []
                continue
            if current is None:
                continue
            match = _LINE_RE.match(stripped)
            if not match:
                continue
            name = match.group("name").upper()
            params = match.group("params") or ""
            value = match.group("value")
            if name == "ATTENDEE":
                attendees.append(_unescape(value).replace("mailto:", "").strip())
            else:
                current[name] = (params, value)
        return events

    @staticmethod
    def _to_event(
        fields: dict[str, tuple[str, str]], attendees: list[str], calendar: str
    ) -> CalendarEvent | None:
        try:
            uid = _unescape(fields.get("UID", ("", event_id()))[1])
            summary = _unescape(fields.get("SUMMARY", ("", "(no title)"))[1])
            dtstart_params, dtstart = fields.get("DTSTART", ("", ""))
            if not dtstart:
                return None
            start = _parse_ics_datetime(dtstart, dtstart_params)
            dtend_params, dtend = fields.get("DTEND", ("", ""))
            end = _parse_ics_datetime(dtend, dtend_params) if dtend else start + timedelta(hours=1)
            status = fields.get("STATUS", ("", "CONFIRMED"))[1].upper()
            return CalendarEvent(
                id=uid,
                title=summary,
                start=start,
                end=end,
                all_day="VALUE=DATE" in dtstart_params.upper(),
                location=_unescape(fields.get("LOCATION", ("", ""))[1]) or None,
                description=_unescape(fields.get("DESCRIPTION", ("", ""))[1]) or None,
                participants=tuple(attendees),
                calendar=calendar,
                cancelled=status == "CANCELLED",
            )
        except (ToolError, ValueError):
            return None

    def _write_file(self, path: Path, events: Sequence[CalendarEvent]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            f"PRODID:{PRODID}",
            "CALSCALE:GREGORIAN",
        ]
        stamp = _to_ics_datetime(datetime.now(timezone.utc))
        for event in events:
            lines.append("BEGIN:VEVENT")
            lines.append(f"UID:{event.id}")
            lines.append(f"DTSTAMP:{stamp}")
            lines.append(f"DTSTART:{_to_ics_datetime(event.start)}")
            lines.append(f"DTEND:{_to_ics_datetime(event.end)}")
            lines.append(f"SUMMARY:{_escape(event.title)}")
            if event.location:
                lines.append(f"LOCATION:{_escape(event.location)}")
            if event.description:
                lines.append(f"DESCRIPTION:{_escape(event.description)}")
            for participant in event.participants:
                lines.append(f"ATTENDEE:mailto:{participant}")
            lines.append(f"STATUS:{'CANCELLED' if event.cancelled else 'CONFIRMED'}")
            lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")
        payload = "\r\n".join(_fold(line) for line in lines) + "\r\n"
        tmp = path.with_suffix(".ics.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)

    def _all_events(self) -> list[CalendarEvent]:
        self.directory.mkdir(parents=True, exist_ok=True)
        events: list[CalendarEvent] = []
        for path in sorted(self.directory.glob("*.ics")):
            events.extend(self._read_file(path))
        events.sort(key=lambda e: e.start)
        return events

    # -- operations -----------------------------------------------------------

    async def list_events(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        include_cancelled: bool = False,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        events = await asyncio.to_thread(self._all_events)
        selected = [
            e
            for e in events
            if (include_cancelled or not e.cancelled)
            and (start is None or e.end >= start)
            and (end is None or e.start <= end)
        ]
        return selected[:limit]

    async def search_events(self, query: str, limit: int = 25) -> list[CalendarEvent]:
        needle = query.lower().strip()
        events = await asyncio.to_thread(self._all_events)
        if not needle:
            return events[:limit]
        matches = [
            e
            for e in events
            if needle in e.title.lower()
            or needle in (e.description or "").lower()
            or needle in (e.location or "").lower()
            or any(needle in p.lower() for p in e.participants)
        ]
        return matches[:limit]

    async def get_event(self, event_uid: str) -> CalendarEvent:
        for event in await asyncio.to_thread(self._all_events):
            if event.id == event_uid:
                return event
        raise NotFoundError(f"No calendar event with id {event_uid}.", details={"id": event_uid})

    async def create_event(self, event: CalendarEvent) -> CalendarEvent:
        return await asyncio.to_thread(self._create_sync, event)

    def _create_sync(self, event: CalendarEvent) -> CalendarEvent:
        if event.end <= event.start:
            raise ToolError("The event ends before it starts.")
        path = self._path(event.calendar)
        events = self._read_file(path)
        for existing in events:
            if existing.id == event.id:
                raise ConflictError("An event with that id already exists.")
        events.append(event)
        events.sort(key=lambda e: e.start)
        self._write_file(path, events)
        return event

    async def update_event(self, event_uid: str, **changes: object) -> CalendarEvent:
        return await asyncio.to_thread(self._update_sync, event_uid, changes)

    def _update_sync(self, event_uid: str, changes: dict[str, object]) -> CalendarEvent:
        for path in sorted(self.directory.glob("*.ics")):
            events = self._read_file(path)
            for index, existing in enumerate(events):
                if existing.id != event_uid:
                    continue
                data = existing.model_dump()
                for key, value in changes.items():
                    if value is not None and key in data:
                        data[key] = value
                updated = CalendarEvent.model_validate(data)
                if updated.end <= updated.start:
                    raise ToolError("The event would end before it starts.")
                events[index] = updated
                events.sort(key=lambda e: e.start)
                self._write_file(path, events)
                return updated
        raise NotFoundError(f"No calendar event with id {event_uid}.", details={"id": event_uid})

    async def cancel_event(self, event_uid: str, *, delete: bool = False) -> CalendarEvent:
        return await asyncio.to_thread(self._cancel_sync, event_uid, delete)

    def _cancel_sync(self, event_uid: str, delete: bool) -> CalendarEvent:
        for path in sorted(self.directory.glob("*.ics")):
            events = self._read_file(path)
            for index, existing in enumerate(events):
                if existing.id != event_uid:
                    continue
                cancelled = existing.model_copy(update={"cancelled": True})
                if delete:
                    events.pop(index)
                else:
                    events[index] = cancelled
                self._write_file(path, events)
                return cancelled
        raise NotFoundError(f"No calendar event with id {event_uid}.", details={"id": event_uid})


class MockCalendarProvider(CalendarProvider):
    """In-memory calendar used by tests."""

    name = "mock"
    display_name = "Mock calendar"

    def __init__(self, events: Sequence[CalendarEvent] = ()) -> None:
        self.events: list[CalendarEvent] = list(events)

    def capabilities(self) -> tuple[str, ...]:
        return ("list", "search", "create", "update", "cancel")

    async def health_check(self) -> IntegrationInfo:
        return self.ok(f"{len(self.events)} in-memory event(s)")

    async def list_events(self, **kw: object) -> list[CalendarEvent]:
        include_cancelled = bool(kw.get("include_cancelled", False))
        return [e for e in self.events if include_cancelled or not e.cancelled]

    async def search_events(self, query: str, limit: int = 25) -> list[CalendarEvent]:
        needle = query.lower()
        return [e for e in self.events if needle in e.title.lower()][:limit]

    async def get_event(self, event_uid: str) -> CalendarEvent:
        for event in self.events:
            if event.id == event_uid:
                return event
        raise NotFoundError(f"No calendar event with id {event_uid}.")

    async def create_event(self, event: CalendarEvent) -> CalendarEvent:
        self.events.append(event)
        return event

    async def update_event(self, event_uid: str, **changes: object) -> CalendarEvent:
        event = await self.get_event(event_uid)
        data = event.model_dump()
        data.update({k: v for k, v in changes.items() if v is not None and k in data})
        updated = CalendarEvent.model_validate(data)
        self.events = [updated if e.id == event_uid else e for e in self.events]
        return updated

    async def cancel_event(self, event_uid: str, *, delete: bool = False) -> CalendarEvent:
        event = await self.get_event(event_uid)
        cancelled = event.model_copy(update={"cancelled": True})
        if delete:
            self.events = [e for e in self.events if e.id != event_uid]
        else:
            self.events = [cancelled if e.id == event_uid else e for e in self.events]
        return cancelled
