"""Calendar tools.

Creating and cancelling events both show a full preview first. Cancelling always
requires confirmation and is never batched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, field_validator

from privia_shared.domain import CalendarEvent
from privia_shared.enums import AuditAction, RiskLevel, Scope
from privia_shared.errors import ValidationError
from privia_shared.ids import event_id, utcnow
from privia_shared.tools import ConfirmationRequest, RetryPolicy, ToolResult

from ..context import ToolContext
from ..registry import Tool

MAX_EVENT_DAYS = 30


def _parse_when(value: str | None, *, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(
            f"'{field}' must be an ISO-8601 date-time such as 2026-03-04T15:00:00.",
            details={"field": field, "value": value[:40]},
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ListEventsArgs(BaseModel):
    start: str | None = Field(default=None, description="ISO-8601 start of the window.")
    end: str | None = Field(default=None, description="ISO-8601 end of the window.")
    days: int | None = Field(default=None, ge=1, le=365, description="Shortcut: next N days.")
    include_cancelled: bool = False
    limit: int = Field(default=50, ge=1, le=200)


class CalendarListTool(Tool[ListEventsArgs]):
    name = "calendar.list_events"
    family = "calendar"
    description = "List calendar events in a time window, for example today or the next 7 days."
    scopes = (Scope.CALENDAR_READ,)
    risk_level = RiskLevel.NONE
    Args = ListEventsArgs

    async def execute(self, args: ListEventsArgs, ctx: ToolContext) -> ToolResult:
        start = _parse_when(args.start, field="start")
        end = _parse_when(args.end, field="end")
        if args.days and not end:
            start = start or utcnow()
            end = start + timedelta(days=args.days)
        events = await ctx.providers.calendar.list_events(
            start=start, end=end, include_cancelled=args.include_cancelled, limit=args.limit
        )
        return ToolResult.ok(
            {
                "count": len(events),
                "window": {
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                },
                "events": [e.model_dump(mode="json") for e in events],
            }
        )


class SearchEventsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=25, ge=1, le=100)


class CalendarSearchTool(Tool[SearchEventsArgs]):
    name = "calendar.search_events"
    family = "calendar"
    description = "Search calendar events by title, description, location or participant."
    scopes = (Scope.CALENDAR_READ,)
    risk_level = RiskLevel.NONE
    Args = SearchEventsArgs

    async def execute(self, args: SearchEventsArgs, ctx: ToolContext) -> ToolResult:
        events = await ctx.providers.calendar.search_events(args.query, args.limit)
        return ToolResult.ok(
            {"count": len(events), "events": [e.model_dump(mode="json") for e in events]}
        )


class CreateEventArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(description="ISO-8601 start, e.g. 2026-03-04T15:00:00.")
    end: str | None = Field(default=None, description="ISO-8601 end. Defaults to start + 1 hour.")
    duration_minutes: int | None = Field(default=None, ge=5, le=60 * 24)
    timezone_name: str = Field(default="UTC", max_length=64)
    location: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    participants: list[str] = Field(default_factory=list, max_length=50)
    calendar: str = Field(default="default", max_length=60)

    @field_validator("participants")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if v.strip()]


class CalendarCreateTool(Tool[CreateEventArgs]):
    name = "calendar.create_event"
    family = "calendar"
    description = "Create a calendar event. Always previews the full details for approval first."
    scopes = (Scope.CALENDAR_WRITE,)
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True
    Args = CreateEventArgs

    @staticmethod
    def _window(args: CreateEventArgs) -> tuple[datetime, datetime]:
        start = _parse_when(args.start, field="start")
        if start is None:
            raise ValidationError("A start time is required.")
        end = _parse_when(args.end, field="end")
        if end is None:
            minutes = args.duration_minutes or 60
            end = start + timedelta(minutes=minutes)
        if end <= start:
            raise ValidationError("The event would end before it starts.")
        if end - start > timedelta(days=MAX_EVENT_DAYS):
            raise ValidationError(f"Events longer than {MAX_EVENT_DAYS} days are not supported.")
        return start, end

    def confirmation(self, args: CreateEventArgs, ctx: ToolContext) -> ConfirmationRequest:
        start, end = self._window(args)
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Create this event?",
            summary=f"{args.title} on {start.strftime('%a %d %b %Y at %H:%M')} UTC.",
            risk_level=RiskLevel.MEDIUM,
            details={
                "Title": args.title,
                "Date": start.strftime("%A %d %B %Y"),
                "Time": f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}",
                "Time zone": args.timezone_name,
                "Participants": ", ".join(args.participants) or "none",
                "Location": args.location or "none",
                "Description": (args.description or "none")[:200],
                "Calendar": args.calendar,
            },
            target=args.title,
        )

    async def execute(self, args: CreateEventArgs, ctx: ToolContext) -> ToolResult:
        start, end = self._window(args)
        event = CalendarEvent(
            id=event_id(),
            title=args.title,
            start=start,
            end=end,
            timezone=args.timezone_name,
            location=args.location,
            description=args.description,
            participants=tuple(args.participants),
            calendar=args.calendar,
        )
        created = await ctx.providers.calendar.create_event(event)
        ctx.audit.record(
            AuditAction.CALENDAR_EVENT_CREATED,
            tool_name=self.name,
            target=created.id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"title": created.title, "start": created.start.isoformat()},
        )
        return ToolResult.ok(
            created.model_dump(mode="json"), accessed_resources=(f"event:{created.id}",)
        )


class UpdateEventArgs(BaseModel):
    event_id: str
    title: str | None = Field(default=None, max_length=200)
    start: str | None = None
    end: str | None = None
    location: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=5000)


class CalendarUpdateTool(Tool[UpdateEventArgs]):
    name = "calendar.update_event"
    family = "calendar"
    description = "Change the title, time, location or description of an existing event."
    scopes = (Scope.CALENDAR_WRITE,)
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True
    Args = UpdateEventArgs

    def confirmation(self, args: UpdateEventArgs, ctx: ToolContext) -> ConfirmationRequest:
        changes = {k: v for k, v in args.model_dump().items() if k != "event_id" and v is not None}
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Update this event?",
            summary=f"Change {len(changes)} field(s) on event {args.event_id}.",
            risk_level=RiskLevel.MEDIUM,
            details={k.title(): str(v)[:120] for k, v in changes.items()} or {"Changes": "none"},
            target=args.event_id,
        )

    async def execute(self, args: UpdateEventArgs, ctx: ToolContext) -> ToolResult:
        changes: dict[str, object] = {}
        if args.title is not None:
            changes["title"] = args.title
        if args.start is not None:
            changes["start"] = _parse_when(args.start, field="start")
        if args.end is not None:
            changes["end"] = _parse_when(args.end, field="end")
        if args.location is not None:
            changes["location"] = args.location
        if args.description is not None:
            changes["description"] = args.description
        updated = await ctx.providers.calendar.update_event(args.event_id, **changes)
        return ToolResult.ok(
            updated.model_dump(mode="json"), accessed_resources=(f"event:{updated.id}",)
        )


class CancelEventArgs(BaseModel):
    event_id: str
    delete: bool = Field(
        default=False, description="Remove the event instead of marking cancelled."
    )


class CalendarCancelTool(Tool[CancelEventArgs]):
    name = "calendar.cancel_event"
    family = "calendar"
    description = "Cancel a calendar event. Always requires explicit confirmation."
    scopes = (Scope.CALENDAR_DELETE,)
    risk_level = RiskLevel.HIGH
    requires_confirmation = True
    retry_policy = RetryPolicy(max_attempts=1)
    Args = CancelEventArgs

    def confirmation(self, args: CancelEventArgs, ctx: ToolContext) -> ConfirmationRequest:
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Cancel this event?",
            summary=(
                f"{'Delete' if args.delete else 'Cancel'} event {args.event_id}. "
                "Participants may be notified by your calendar app."
            ),
            risk_level=RiskLevel.HIGH,
            details={
                "Event": args.event_id,
                "Action": "delete permanently" if args.delete else "mark as cancelled",
            },
            target=args.event_id,
            destructive=True,
        )

    async def execute(self, args: CancelEventArgs, ctx: ToolContext) -> ToolResult:
        event = await ctx.providers.calendar.cancel_event(args.event_id, delete=args.delete)
        ctx.audit.record(
            AuditAction.CALENDAR_EVENT_CANCELLED,
            tool_name=self.name,
            target=event.id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"title": event.title, "deleted": args.delete},
        )
        return ToolResult.ok(
            event.model_dump(mode="json"), accessed_resources=(f"event:{event.id}",)
        )


CALENDAR_TOOLS = [
    CalendarListTool(),
    CalendarSearchTool(),
    CalendarCreateTool(),
    CalendarUpdateTool(),
    CalendarCancelTool(),
]
