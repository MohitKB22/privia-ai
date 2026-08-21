"""Read-only filesystem browsing for the Files screen.

Every path goes through the same :class:`~privia_security.PathGuard` the tools
use, so this endpoint cannot see anything the assistant could not see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from ..deps import ContainerDep

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("/roots", summary="Folders you have allowed")
async def roots(container: ContainerDep) -> dict[str, Any]:
    return {
        "roots": [
            {"path": str(p), "exists": p.exists(), "name": p.name or str(p)}
            for p in container.providers.path_guard.roots
        ]
    }


@router.get("/list", summary="List a folder")
async def list_directory(
    container: ContainerDep,
    path: str = Query(..., max_length=4096),
    include_hidden: bool = Query(default=False),
) -> dict[str, Any]:
    entries = await container.providers.files.list_directory(
        Path(path), include_hidden=include_hidden
    )
    return {
        "path": path,
        "count": len(entries),
        "entries": [e.model_dump(mode="json") for e in entries],
    }


@router.get("/search", summary="Search allowed folders")
async def search(
    container: ContainerDep,
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    contents: bool = Query(default=False),
) -> dict[str, Any]:
    entries = await container.providers.files.search(
        query, max_results=limit, include_content=contents
    )
    return {"count": len(entries), "files": [e.model_dump(mode="json") for e in entries]}


@router.get("/read", summary="Read a file")
async def read(
    container: ContainerDep,
    path: str = Query(..., max_length=4096),
) -> dict[str, Any]:
    content = await container.providers.files.read(Path(path))
    container.audit.record("file.accessed", tool_name="api.files.read", target=content.path)
    return content.model_dump(mode="json")


@router.get("/metadata", summary="File metadata")
async def metadata(
    container: ContainerDep,
    path: str = Query(..., max_length=4096),
    hash_contents: bool = Query(default=False),
) -> dict[str, Any]:
    meta = await container.providers.files.metadata(Path(path), hash_contents=hash_contents)
    return meta.model_dump(mode="json")
