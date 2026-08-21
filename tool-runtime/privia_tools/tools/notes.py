"""Note tools. Notes are local, first-class objects with full-text search."""

from __future__ import annotations

from pydantic import BaseModel, Field

from privia_integrations.files.local import summarize_text
from privia_shared.enums import RiskLevel, Scope
from privia_shared.tools import ToolResult

from ..context import ToolContext
from ..registry import Tool


class NoteCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    pinned: bool = False


class NotesCreateTool(Tool[NoteCreateArgs]):
    name = "notes.create"
    family = "notes"
    description = "Create a new note with a title, body and optional tags."
    scopes = (Scope.NOTES_WRITE,)
    risk_level = RiskLevel.LOW
    Args = NoteCreateArgs

    async def execute(self, args: NoteCreateArgs, ctx: ToolContext) -> ToolResult:
        note = await ctx.providers.notes.create(
            args.title, args.body, [t.strip() for t in args.tags if t.strip()], args.pinned
        )
        return ToolResult.ok(note.model_dump(mode="json"), accessed_resources=(f"note:{note.id}",))


class NoteSearchArgs(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


class NotesSearchTool(Tool[NoteSearchArgs]):
    name = "notes.search"
    family = "notes"
    description = "Search your notes by title and body using local full-text search."
    scopes = (Scope.NOTES_READ,)
    risk_level = RiskLevel.NONE
    Args = NoteSearchArgs

    async def execute(self, args: NoteSearchArgs, ctx: ToolContext) -> ToolResult:
        notes = (
            await ctx.providers.notes.search(args.query, args.limit)
            if args.query
            else await ctx.providers.notes.list(args.limit)
        )
        return ToolResult.ok(
            {"count": len(notes), "notes": [n.model_dump(mode="json") for n in notes]}
        )


class NoteReadArgs(BaseModel):
    note_id: str


class NotesReadTool(Tool[NoteReadArgs]):
    name = "notes.read"
    family = "notes"
    description = "Read one note in full by its id."
    scopes = (Scope.NOTES_READ,)
    risk_level = RiskLevel.NONE
    Args = NoteReadArgs

    async def execute(self, args: NoteReadArgs, ctx: ToolContext) -> ToolResult:
        note = await ctx.providers.notes.get(args.note_id)
        return ToolResult.ok(note.model_dump(mode="json"), accessed_resources=(f"note:{note.id}",))


class NoteUpdateArgs(BaseModel):
    note_id: str
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=200_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    pinned: bool | None = None
    append: bool = Field(default=False, description="Append body instead of replacing it.")


class NotesUpdateTool(Tool[NoteUpdateArgs]):
    name = "notes.update"
    family = "notes"
    description = "Update a note's title, body, tags or pinned state."
    scopes = (Scope.NOTES_WRITE,)
    risk_level = RiskLevel.LOW
    Args = NoteUpdateArgs

    async def execute(self, args: NoteUpdateArgs, ctx: ToolContext) -> ToolResult:
        note = await ctx.providers.notes.update(
            args.note_id,
            title=args.title,
            body=args.body,
            tags=args.tags,
            pinned=args.pinned,
            append=args.append,
        )
        return ToolResult.ok(note.model_dump(mode="json"), accessed_resources=(f"note:{note.id}",))


class NoteTagArgs(BaseModel):
    note_id: str
    tags: list[str] = Field(min_length=1, max_length=20)
    replace: bool = False


class NotesTagTool(Tool[NoteTagArgs]):
    name = "notes.tag"
    family = "notes"
    description = "Add tags to a note, or replace its tags entirely."
    scopes = (Scope.NOTES_WRITE,)
    risk_level = RiskLevel.LOW
    Args = NoteTagArgs

    async def execute(self, args: NoteTagArgs, ctx: ToolContext) -> ToolResult:
        existing = await ctx.providers.notes.get(args.note_id)
        clean = [t.strip() for t in args.tags if t.strip()]
        tags = clean if args.replace else sorted({*existing.tags, *clean})
        note = await ctx.providers.notes.update(args.note_id, tags=tags)
        return ToolResult.ok(note.model_dump(mode="json"), accessed_resources=(f"note:{note.id}",))


class NoteSummarizeArgs(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    max_sentences: int = Field(default=6, ge=1, le=20)


class NotesSummarizeTool(Tool[NoteSummarizeArgs]):
    name = "notes.summarize"
    family = "notes"
    description = "Summarise the notes matching a query, locally."
    scopes = (Scope.NOTES_READ,)
    risk_level = RiskLevel.NONE
    Args = NoteSummarizeArgs

    async def execute(self, args: NoteSummarizeArgs, ctx: ToolContext) -> ToolResult:
        notes = (
            await ctx.providers.notes.search(args.query, args.limit)
            if args.query
            else await ctx.providers.notes.list(args.limit)
        )
        if not notes:
            return ToolResult.ok({"count": 0, "summary": "There are no notes matching that."})
        combined = "\n\n".join(f"{n.title}. {n.body}" for n in notes)
        return ToolResult.ok(
            {
                "count": len(notes),
                "titles": [n.title for n in notes],
                "summary": summarize_text(combined, max_sentences=args.max_sentences),
                "method": "extractive-local",
            }
        )


NOTE_TOOLS = [
    NotesCreateTool(),
    NotesSearchTool(),
    NotesReadTool(),
    NotesUpdateTool(),
    NotesTagTool(),
    NotesSummarizeTool(),
]
