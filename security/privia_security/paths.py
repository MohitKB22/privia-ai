"""Filesystem path safety.

Every filesystem access in PRIVIA goes through :class:`PathGuard`. The guard
answers one question: *is this concrete, fully resolved path inside a directory
the user explicitly allowed, and is it not a sensitive location?*

Design notes:

* Resolution happens **before** the allowlist check, so ``../`` sequences and
  symlinks cannot be used to escape a root.
* The root itself is resolved too, so a symlinked allowed root still works.
* Sensitive directory and file names are denied even inside an allowed root:
  granting access to your home folder must not hand over ``~/.ssh``.
* The guard never touches the filesystem to *create* anything; it only inspects.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from privia_shared.errors import PathNotAllowedError, PathTraversalError

#: Directory names that are never readable, even inside an allowed root.
SENSITIVE_DIR_NAMES = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".config/gcloud",
        ".kube",
        ".docker",
        ".password-store",
        ".gem/credentials",
        ".netrc.d",
        "keychains",
        "library/keychains",
        ".local/share/keyrings",
        ".mozilla",
        ".thunderbird",
        ".pki",
        "node_modules",
        ".git",
    }
)

#: Exact file names that are never readable.
SENSITIVE_FILE_NAMES = frozenset(
    {
        ".netrc",
        ".pgpass",
        ".htpasswd",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "credentials",
        "shadow",
        "master.key",
        ".env",
        ".env.local",
        ".env.production",
        "secrets.json",
        "privia_secrets.enc",
    }
)

#: Suffixes that commonly hold private key material.
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".kdbx"})

#: Absolute prefixes that are always off-limits regardless of configuration.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/var/run",
    "/private/etc",
    "/System",
    "/Library/Keychains",
    "C:\\Windows",
)


@dataclass(frozen=True)
class PathDecision:
    """Result of a path check."""

    path: Path
    root: Path
    allowed: bool
    reason: str = ""

    def raise_for_status(self) -> Path:
        if not self.allowed:
            raise PathNotAllowedError(self.reason, details={"path": str(self.path)})
        return self.path


class PathGuard:
    """Validates paths against a set of allowed roots."""

    def __init__(
        self,
        allowed_roots: Iterable[Path | str] = (),
        *,
        follow_symlinks: bool = False,
        max_file_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.follow_symlinks = follow_symlinks
        self.max_file_bytes = max_file_bytes
        self._roots: tuple[Path, ...] = self._normalise_roots(allowed_roots)

    # -- roots ---------------------------------------------------------------

    @staticmethod
    def _normalise_roots(roots: Iterable[Path | str]) -> tuple[Path, ...]:
        resolved: list[Path] = []
        for raw in roots:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                continue
            try:
                resolved.append(path.resolve(strict=False))
            except OSError:  # pragma: no cover - unreadable mount points
                continue
        # Drop roots that are nested inside another root to keep checks cheap.
        unique: list[Path] = []
        for candidate in sorted(set(resolved), key=lambda p: len(p.parts)):
            if any(_is_within(candidate, existing) for existing in unique):
                continue
            unique.append(candidate)
        return tuple(unique)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def set_roots(self, roots: Iterable[Path | str]) -> None:
        self._roots = self._normalise_roots(roots)

    def add_root(self, root: Path | str) -> None:
        self.set_roots([*self._roots, root])

    # -- checks --------------------------------------------------------------

    def check(self, candidate: Path | str, *, must_exist: bool = False) -> PathDecision:
        """Validate ``candidate`` and return a decision (never raises for policy)."""
        raw = str(candidate).strip()
        if not raw:
            return PathDecision(Path("."), Path("."), False, "An empty path is not valid.")
        if "\x00" in raw:
            return PathDecision(Path(raw), Path("."), False, "Path contains a null byte.")

        expanded = Path(raw).expanduser()
        if not expanded.is_absolute():
            return PathDecision(
                expanded,
                Path("."),
                False,
                "Only absolute paths are accepted; relative paths are ambiguous.",
            )

        try:
            resolved = expanded.resolve(strict=False)
        except (OSError, RuntimeError) as exc:  # RuntimeError = symlink loop
            return PathDecision(expanded, Path("."), False, f"Path could not be resolved: {exc}")

        for prefix in FORBIDDEN_PREFIXES:
            if _is_within(resolved, Path(prefix)):
                return PathDecision(
                    resolved,
                    Path(prefix),
                    False,
                    f"'{prefix}' holds system configuration and is permanently blocked.",
                )

        if not self._roots:
            return PathDecision(
                resolved,
                Path("."),
                False,
                "No folders have been allowed yet. Grant a folder in the Privacy Center first.",
            )

        root = self._matching_root(resolved)
        if root is None:
            return PathDecision(
                resolved,
                Path("."),
                False,
                "That path is outside the folders you have allowed.",
            )

        sensitive = self._sensitive_reason(resolved, root)
        if sensitive:
            return PathDecision(resolved, root, False, sensitive)

        if not self.follow_symlinks and _has_symlink_component(expanded, root):
            return PathDecision(
                resolved,
                root,
                False,
                "The path traverses a symbolic link, which could point outside the allowed folder.",
            )

        if must_exist and not resolved.exists():
            return PathDecision(resolved, root, False, "That path does not exist.")

        return PathDecision(resolved, root, True)

    def resolve(self, candidate: Path | str, *, must_exist: bool = False) -> Path:
        """Validate and return the resolved path, raising on refusal."""
        decision = self.check(candidate, must_exist=must_exist)
        return decision.raise_for_status()

    def assert_within_root(self, candidate: Path | str, root: Path | str) -> Path:
        """Confirm ``candidate`` stays under ``root`` (used for move/rename)."""
        resolved = Path(candidate).expanduser().resolve(strict=False)
        root_resolved = Path(root).expanduser().resolve(strict=False)
        if not _is_within(resolved, root_resolved):
            raise PathTraversalError(
                "The destination escapes its allowed folder.",
                details={"path": str(resolved), "root": str(root_resolved)},
            )
        return resolved

    def check_size(self, path: Path) -> int:
        """Return the file size, refusing files above the configured limit."""
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PathNotAllowedError(
                "The file could not be inspected.", details={"path": str(path), "error": str(exc)}
            ) from exc
        if size > self.max_file_bytes:
            raise PathNotAllowedError(
                f"That file is {size:,} bytes, above the {self.max_file_bytes:,} byte limit.",
                details={"path": str(path), "size_bytes": size, "limit": self.max_file_bytes},
            )
        return size

    def is_regular_file(self, path: Path) -> bool:
        """Reject FIFOs, devices and sockets, which can hang a read forever."""
        try:
            mode = path.lstat().st_mode
        except OSError:
            return False
        return stat.S_ISREG(mode)

    # -- internals -----------------------------------------------------------

    def _matching_root(self, resolved: Path) -> Path | None:
        for root in self._roots:
            if _is_within(resolved, root):
                return root
        return None

    @staticmethod
    def _sensitive_reason(resolved: Path, root: Path) -> str:
        try:
            relative = resolved.relative_to(root)
        except ValueError:  # pragma: no cover - guarded by caller
            relative = resolved
        parts_lower = [p.lower() for p in relative.parts]
        for index, part in enumerate(parts_lower):
            if part in SENSITIVE_DIR_NAMES:
                return f"'{relative.parts[index]}' is a sensitive location and is never read."
            two = "/".join(parts_lower[index : index + 2])
            if two in SENSITIVE_DIR_NAMES:
                return f"'{two}' is a sensitive location and is never read."
        name = resolved.name.lower()
        if name in SENSITIVE_FILE_NAMES:
            return f"'{resolved.name}' can hold credentials and is never read."
        if resolved.suffix.lower() in SENSITIVE_SUFFIXES:
            return f"'{resolved.suffix}' files can hold private keys and are never read."
        return ""


def _is_within(candidate: PurePath, root: PurePath) -> bool:
    """True when ``candidate`` is ``root`` or lives under it."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _has_symlink_component(path: Path, root: Path) -> bool:
    """Walk from ``root`` down to ``path`` looking for a symlink component."""
    try:
        current = path.expanduser()
        seen: set[Path] = set()
        while current != current.parent and current not in seen:
            seen.add(current)
            if current.is_symlink():
                return True
            if current == root:
                break
            current = current.parent
    except OSError:  # pragma: no cover
        return True
    return False


def safe_join(root: Path, *parts: str) -> Path:
    """Join user-supplied components to a root without escaping it."""
    candidate = root.joinpath(*parts).resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if not _is_within(candidate, root_resolved):
        raise PathTraversalError(details={"root": str(root_resolved), "path": str(candidate)})
    return candidate


def sanitize_filename(name: str, *, fallback: str = "untitled") -> str:
    """Strip directory separators and control characters from a file name."""
    cleaned = "".join(c for c in name if c.isprintable() and c not in '/\\:*?"<>|\x00')
    cleaned = cleaned.replace(os.sep, "_").strip(" .")
    cleaned = cleaned[:180]
    return cleaned or fallback


def describe_roots(roots: Sequence[Path]) -> str:
    if not roots:
        return "no folders allowed"
    return ", ".join(str(r) for r in roots)
