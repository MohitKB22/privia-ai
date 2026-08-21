"""Permission inspection and grants."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from privia_security.policy import describe_scope
from privia_shared.enums import AuditAction, PermissionGrantState, Scope
from privia_shared.errors import BadRequestError, PathNotAllowedError
from privia_shared.permissions import PermissionGrant, PermissionUpdate

from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1/permissions", tags=["permissions"])


class DirectoryRequest(BaseModel):
    path: str


@router.get("", summary="List every scope and its state")
async def list_permissions(container: ContainerDep) -> dict[str, Any]:
    grants = {g.scope: g for g in container.permissions.all_grants()}
    return {
        "scopes": [_describe(scope, grants.get(scope)) for scope in Scope],
        "allowed_directories": [str(p) for p in container.providers.path_guard.roots],
        "terminal_roots": [str(p) for p in container.providers.command_guard.workspace_roots],
    }


def _describe(scope: Scope, grant: PermissionGrant | None) -> dict[str, Any]:
    """Render one scope for the Privacy Center."""
    expires_at = grant.expires_at if grant else None
    return {
        "scope": scope.value,
        "family": scope.family,
        "description": describe_scope(scope),
        "state": str(grant.state if grant else PermissionGrantState.NOT_REQUESTED),
        "resources": list(grant.resources) if grant else [],
        "session_only": grant.session_only if grant else False,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.post("", summary="Grant or revoke a scope")
async def update_permission(
    body: PermissionUpdate, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    if body.grant:
        grant = container.permissions.grant(
            body.scope,
            resources=body.resources,
            session_only=body.session_only,
            ttl_seconds=body.ttl_seconds,
        )
        container.audit.permission_granted(body.scope.value, request_id=request_id)
    else:
        grant = container.permissions.deny(body.scope, note="Revoked by the user.")
        container.audit.record(
            AuditAction.PERMISSION_REVOKED, target=body.scope.value, request_id=request_id
        )
    container.persist_grant(body.scope)
    return {
        "scope": body.scope.value,
        "state": str(grant.state),
        "resources": list(grant.resources),
        "description": describe_scope(body.scope),
    }


def _resolve_directory(raw: str) -> tuple[Path | None, bool]:
    """Expand, resolve and stat a path. Runs on a worker thread."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        return None, False
    resolved = candidate.resolve(strict=False)
    return resolved, resolved.is_dir()


@router.post("/directories", summary="Allow a folder")
async def add_directory(
    body: DirectoryRequest, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    """Add a folder to the filesystem allowlist.

    The path must exist, be absolute, and not be the filesystem root or a system
    directory. Granting a folder does not grant any scope: the user still has to
    allow ``files:read`` before anything is read.
    """
    # Resolution and stat() can block for seconds on a network mount, so the
    # whole filesystem interaction happens on a worker thread.
    resolved, is_directory = await asyncio.to_thread(_resolve_directory, body.path)
    if resolved is None:
        raise BadRequestError("Give an absolute path.")
    if not is_directory:
        raise BadRequestError(f"'{resolved}' is not a folder that exists.")
    if str(resolved) in ("/", str(Path.home().anchor)):
        raise PathNotAllowedError("The filesystem root cannot be allowed.")
    for forbidden in ("/etc", "/sys", "/proc", "/dev", "/System", "/Library/Keychains"):
        if str(resolved) == forbidden or str(resolved).startswith(forbidden + "/"):
            raise PathNotAllowedError(f"'{forbidden}' holds system files and cannot be allowed.")

    directories = container.grant_directory(str(resolved))
    container.audit.record(
        AuditAction.SETTINGS_CHANGED,
        target=str(resolved),
        request_id=request_id,
        detail={"allowed_directories": directories},
    )
    return {"allowed_directories": directories}


@router.delete("/directories", summary="Stop allowing a folder")
async def remove_directory(
    body: DirectoryRequest, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    directories = container.revoke_directory(str(body.path))
    container.audit.record(
        AuditAction.SETTINGS_CHANGED,
        target=body.path,
        request_id=request_id,
        detail={"allowed_directories": directories, "removed": body.path},
    )
    return {"allowed_directories": directories}


@router.post("/reset", summary="Revoke everything")
async def reset(container: ContainerDep, request_id: RequestIdDep) -> dict[str, Any]:
    removed = container.repositories.permissions.delete_all()
    container.permissions.load([])
    container.permissions.forget_confirmations()
    container.audit.record(
        AuditAction.PERMISSION_REVOKED,
        target="all",
        request_id=request_id,
        detail={"removed": removed},
    )
    return {"revoked": removed}
