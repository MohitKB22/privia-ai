"""File tools.

Reading is low risk and needs ``files:read``. Writing needs ``files:write``.
Deleting needs ``files:delete`` *and* an explicit confirmation showing the exact
absolute path, every single time, with no "remember this" option.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from privia_integrations.files.local import summarize_text
from privia_shared.enums import AuditAction, RiskLevel, Scope
from privia_shared.errors import PathNotAllowedError
from privia_shared.tools import ConfirmationRequest, RetryPolicy, ToolResult

from ..context import ToolContext
from ..registry import Tool

#: Longest path any real filesystem accepts is well under this. Bounding the
#: field at the schema layer means an absurd value is rejected by validation,
#: before it reaches the permission engine or the filesystem.
MAX_PATH_CHARS = 4096


class SearchArgs(BaseModel):
    query: str = Field(description="Text to look for in file names.", max_length=200)
    extensions: list[str] = Field(
        default_factory=list,
        description="Limit to these extensions, e.g. ['.md', '.pdf'].",
        max_length=12,
    )
    search_contents: bool = Field(
        default=False, description="Also look inside text files (slower)."
    )
    limit: int = Field(default=25, ge=1, le=200)


class FilesSearchTool(Tool[SearchArgs]):
    name = "files.search"
    family = "files"
    description = "Find files by name (and optionally by content) inside the allowed folders."
    scopes = (Scope.FILES_READ,)
    risk_level = RiskLevel.LOW
    timeout_seconds = 30.0
    Args = SearchArgs

    def resources(self, args: SearchArgs, ctx: ToolContext) -> tuple[str, ...]:
        return tuple(str(r) for r in ctx.providers.path_guard.roots)

    async def execute(self, args: SearchArgs, ctx: ToolContext) -> ToolResult:
        entries = await ctx.providers.files.search(
            args.query,
            extensions=args.extensions,
            max_results=args.limit,
            include_content=args.search_contents,
        )
        for entry in entries[:20]:
            ctx.note_resource(entry.path)
        return ToolResult.ok(
            {"count": len(entries), "files": [e.model_dump(mode="json") for e in entries]},
            accessed_resources=tuple(e.path for e in entries[:20]),
            metadata={"roots": [str(r) for r in ctx.providers.path_guard.roots]},
        )


class ListArgs(BaseModel):
    path: str = Field(description="Absolute path of the folder to list.", max_length=MAX_PATH_CHARS)
    include_hidden: bool = False


class FilesListTool(Tool[ListArgs]):
    name = "files.list_directory"
    family = "files"
    description = "List the contents of a folder inside the allowed folders."
    scopes = (Scope.FILES_READ,)
    risk_level = RiskLevel.LOW
    Args = ListArgs

    def resources(self, args: ListArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    async def execute(self, args: ListArgs, ctx: ToolContext) -> ToolResult:
        entries = await ctx.providers.files.list_directory(
            Path(args.path), include_hidden=args.include_hidden
        )
        return ToolResult.ok(
            {
                "path": args.path,
                "count": len(entries),
                "entries": [e.model_dump(mode="json") for e in entries],
            },
            accessed_resources=(args.path,),
        )


class ReadArgs(BaseModel):
    path: str = Field(description="Absolute path of the file to read.", max_length=MAX_PATH_CHARS)
    max_bytes: int | None = Field(default=None, ge=1, le=64 * 1024 * 1024)


class FilesReadTool(Tool[ReadArgs]):
    name = "files.read"
    family = "files"
    description = "Read the text contents of a file inside the allowed folders."
    scopes = (Scope.FILES_READ,)
    risk_level = RiskLevel.LOW
    #: File contents are data, not instructions.
    returns_untrusted_content = True
    Args = ReadArgs

    def resources(self, args: ReadArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    async def execute(self, args: ReadArgs, ctx: ToolContext) -> ToolResult:
        content = await ctx.providers.files.read(Path(args.path), max_bytes=args.max_bytes)
        ctx.audit.record(
            AuditAction.FILE_ACCESSED,
            tool_name=self.name,
            target=content.path,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
        )
        return ToolResult.ok(
            content.model_dump(mode="json"),
            accessed_resources=(content.path,),
            truncated=content.truncated,
        )


class MetadataArgs(BaseModel):
    path: str = Field(max_length=MAX_PATH_CHARS)
    hash_contents: bool = False


class FilesMetadataTool(Tool[MetadataArgs]):
    name = "files.metadata"
    family = "files"
    description = "Get size, timestamps, type and optional checksum for a file."
    scopes = (Scope.FILES_READ,)
    risk_level = RiskLevel.LOW
    Args = MetadataArgs

    def resources(self, args: MetadataArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    async def execute(self, args: MetadataArgs, ctx: ToolContext) -> ToolResult:
        meta = await ctx.providers.files.metadata(Path(args.path), hash_contents=args.hash_contents)
        return ToolResult.ok(meta.model_dump(mode="json"), accessed_resources=(meta.path,))


class SummarizeArgs(BaseModel):
    path: str = Field(max_length=MAX_PATH_CHARS)
    max_sentences: int = Field(default=6, ge=1, le=20)


class FilesSummarizeTool(Tool[SummarizeArgs]):
    name = "files.summarize"
    family = "files"
    description = (
        "Summarise a text document locally without sending it anywhere. Uses the language "
        "model when one is available and a deterministic extractive summary otherwise."
    )
    scopes = (Scope.FILES_READ,)
    risk_level = RiskLevel.LOW
    returns_untrusted_content = True
    timeout_seconds = 60.0
    Args = SummarizeArgs

    def resources(self, args: SummarizeArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    async def execute(self, args: SummarizeArgs, ctx: ToolContext) -> ToolResult:
        content = await ctx.providers.files.read(Path(args.path))
        ctx.audit.record(
            AuditAction.FILE_ACCESSED,
            tool_name=self.name,
            target=content.path,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
        )
        summary = summarize_text(content.text, max_sentences=args.max_sentences)
        return ToolResult.ok(
            {
                "path": content.path,
                "summary": summary,
                "characters": len(content.text),
                "truncated": content.truncated,
                "method": "extractive-local",
            },
            accessed_resources=(content.path,),
        )


class CreateArgs(BaseModel):
    path: str = Field(description="Absolute path of the file to create.", max_length=MAX_PATH_CHARS)
    content: str = Field(default="", max_length=1_000_000)
    overwrite: bool = False


class FilesCreateTool(Tool[CreateArgs]):
    name = "files.create"
    family = "files"
    description = "Create or overwrite a text file inside the allowed folders."
    scopes = (Scope.FILES_WRITE,)
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True
    confirmation_template = "Create {path}"
    Args = CreateArgs

    def resources(self, args: CreateArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    def confirmation(self, args: CreateArgs, ctx: ToolContext) -> ConfirmationRequest:
        exists = Path(args.path).exists()
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Overwrite this file?" if exists else "Create this file?",
            summary=(
                f"{'Overwrite' if exists else 'Create'} {args.path} "
                f"({len(args.content):,} characters)."
            ),
            risk_level=RiskLevel.MEDIUM if not exists else RiskLevel.HIGH,
            details={
                "Path": args.path,
                "Size": f"{len(args.content):,} characters",
                "Existing file": "yes, it will be replaced" if exists else "no",
            },
            target=args.path,
            destructive=exists,
        )

    async def execute(self, args: CreateArgs, ctx: ToolContext) -> ToolResult:
        meta = await ctx.providers.files.create(
            Path(args.path), args.content, overwrite=args.overwrite
        )
        ctx.audit.record(
            AuditAction.FILE_MODIFIED,
            tool_name=self.name,
            target=meta.path,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"bytes": meta.size_bytes},
        )
        return ToolResult.ok(meta.model_dump(mode="json"), accessed_resources=(meta.path,))


class RenameArgs(BaseModel):
    path: str = Field(max_length=MAX_PATH_CHARS)
    new_name: str = Field(max_length=200, description="New file name, not a path.")


class FilesRenameTool(Tool[RenameArgs]):
    name = "files.rename"
    family = "files"
    description = "Rename a file, keeping it in the same folder."
    scopes = (Scope.FILES_WRITE,)
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True
    Args = RenameArgs

    def resources(self, args: RenameArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    def confirmation(self, args: RenameArgs, ctx: ToolContext) -> ConfirmationRequest:
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Rename this file?",
            summary=f"Rename {Path(args.path).name} to {args.new_name}.",
            risk_level=RiskLevel.MEDIUM,
            details={"From": args.path, "To": args.new_name},
            target=args.path,
        )

    async def execute(self, args: RenameArgs, ctx: ToolContext) -> ToolResult:
        meta = await ctx.providers.files.rename(Path(args.path), args.new_name)
        ctx.audit.record(
            AuditAction.FILE_MODIFIED,
            tool_name=self.name,
            target=meta.path,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"from": args.path},
        )
        return ToolResult.ok(
            meta.model_dump(mode="json"), accessed_resources=(args.path, meta.path)
        )


class MoveArgs(BaseModel):
    path: str = Field(max_length=MAX_PATH_CHARS)
    destination_dir: str = Field(max_length=MAX_PATH_CHARS)


class FilesMoveTool(Tool[MoveArgs]):
    name = "files.move"
    family = "files"
    description = "Move a file into another allowed folder."
    scopes = (Scope.FILES_WRITE,)
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True
    Args = MoveArgs

    def resources(self, args: MoveArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path, args.destination_dir)

    def confirmation(self, args: MoveArgs, ctx: ToolContext) -> ConfirmationRequest:
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Move this file?",
            summary=f"Move {Path(args.path).name} into {args.destination_dir}.",
            risk_level=RiskLevel.MEDIUM,
            details={"File": args.path, "Destination": args.destination_dir},
            target=args.path,
        )

    async def execute(self, args: MoveArgs, ctx: ToolContext) -> ToolResult:
        meta = await ctx.providers.files.move(Path(args.path), Path(args.destination_dir))
        ctx.audit.record(
            AuditAction.FILE_MODIFIED,
            tool_name=self.name,
            target=meta.path,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"from": args.path},
        )
        return ToolResult.ok(
            meta.model_dump(mode="json"), accessed_resources=(args.path, meta.path)
        )


class DeleteArgs(BaseModel):
    path: str = Field(
        description="Absolute path of the single file to delete.", max_length=MAX_PATH_CHARS
    )


class FilesDeleteTool(Tool[DeleteArgs]):
    name = "files.delete"
    family = "files"
    description = (
        "Permanently delete ONE file. Always shows the exact path and requires explicit "
        "confirmation. Never deletes folders and never deletes in bulk."
    )
    scopes = (Scope.FILES_DELETE,)
    risk_level = RiskLevel.CRITICAL
    requires_confirmation = True
    #: Deleting is never retried: a partial failure must not be repeated blindly.
    retry_policy = RetryPolicy(max_attempts=1)
    Args = DeleteArgs

    def resources(self, args: DeleteArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (args.path,)

    def confirmation(self, args: DeleteArgs, ctx: ToolContext) -> ConfirmationRequest:
        path = Path(args.path)
        try:
            size = path.stat().st_size if path.exists() else 0
        except OSError:
            size = 0
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Permanently delete this file?",
            summary=f"Delete {args.path}. This cannot be undone.",
            risk_level=RiskLevel.CRITICAL,
            details={
                "Path": args.path,
                "Size": f"{size:,} bytes",
                "Recoverable": "No. The file is removed, not moved to the trash.",
            },
            target=args.path,
            destructive=True,
        )

    async def execute(self, args: DeleteArgs, ctx: ToolContext) -> ToolResult:
        decision = ctx.providers.path_guard.check(args.path, must_exist=True)
        if not decision.allowed:
            raise PathNotAllowedError(decision.reason, details={"path": args.path})
        deleted = await ctx.providers.files.delete(Path(args.path))
        ctx.audit.record(
            AuditAction.FILE_DELETED,
            tool_name=self.name,
            target=deleted,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
        )
        return ToolResult.ok({"deleted": deleted}, accessed_resources=(deleted,))


FILE_TOOLS = [
    FilesSearchTool(),
    FilesListTool(),
    FilesReadTool(),
    FilesMetadataTool(),
    FilesSummarizeTool(),
    FilesCreateTool(),
    FilesRenameTool(),
    FilesMoveTool(),
    FilesDeleteTool(),
]
