"""Local filesystem adapter.

All path validation is delegated to :class:`privia_security.PathGuard`; this
adapter assumes it receives paths that have already been resolved and approved,
and re-validates anyway because defence in depth is cheap here.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
import shutil
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from privia_security.paths import PathGuard, sanitize_filename
from privia_shared.domain import FileContent, FileEntry, FileMetadata, IntegrationInfo
from privia_shared.errors import ConflictError, NotFoundError, PathNotAllowedError, ToolError

from ..base import FilesystemProvider

#: Extensions we will attempt to read as text.
TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".log",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".py",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".sh",
        ".sql",
        ".graphql",
        ".proto",
        ".env.example",
        ".gitignore",
        ".dockerfile",
        ".tf",
        ".swift",
        ".m",
        ".r",
        ".jl",
        ".lua",
        ".vim",
        ".el",
    }
)

SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".tox",
        ".idea",
        ".vscode",
    }
)


class LocalFilesystemProvider(FilesystemProvider):
    name = "local"
    display_name = "Local files"

    def __init__(self, guard: PathGuard, *, max_results: int = 200) -> None:
        self.guard = guard
        self.max_results = max_results

    def capabilities(self) -> tuple[str, ...]:
        return ("search", "list", "read", "create", "rename", "move", "delete", "metadata")

    async def health_check(self) -> IntegrationInfo:
        roots = self.guard.roots
        if not roots:
            return self.not_configured(
                "No folders allowed yet. Grant one in the Privacy Center to enable file tools."
            )
        missing = [str(r) for r in roots if not r.exists()]
        if missing:
            return self.unavailable(f"Allowed folder(s) not found: {', '.join(missing[:3])}")
        return self.ok(f"{len(roots)} folder(s) allowed")

    # -- read-only operations -------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        roots: Sequence[Path] | None = None,
        extensions: Sequence[str] = (),
        max_results: int | None = None,
        include_content: bool = False,
    ) -> list[FileEntry]:
        return await asyncio.to_thread(
            self._search_sync, query, roots, extensions, max_results, include_content
        )

    def _search_sync(
        self,
        query: str,
        roots: Sequence[Path] | None,
        extensions: Sequence[str],
        max_results: int | None,
        include_content: bool,
    ) -> list[FileEntry]:
        limit = max_results or self.max_results
        search_roots = list(roots or self.guard.roots)
        if not search_roots:
            raise PathNotAllowedError(
                "No folders have been allowed yet. Grant a folder in the Privacy Center first."
            )
        needle = query.lower().strip()
        tokens = [t for t in _WORD_RE.findall(needle) if len(t) > 1]
        wanted_ext = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
        results: list[FileEntry] = []
        scored: list[tuple[float, FileEntry]] = []
        seen: set[Path] = set()

        for root in search_roots:
            if len(results) >= limit:
                break
            decision = self.guard.check(root)
            if not decision.allowed:
                continue
            for dirpath, dirnames, filenames in os.walk(decision.path, followlinks=False):
                dirnames[:] = [
                    d for d in dirnames if d not in SKIP_DIRECTORIES and not d.startswith(".")
                ]
                current = Path(dirpath)
                for filename in filenames:
                    if len(results) >= limit:
                        break
                    candidate = current / filename
                    if candidate in seen:
                        continue
                    suffix = candidate.suffix.lower()
                    if wanted_ext and suffix not in wanted_ext:
                        continue
                    name_score = _name_score(filename, needle, tokens)
                    content_match = False
                    if name_score <= 0 and include_content and suffix in TEXT_EXTENSIONS:
                        content_match = self._contains(candidate, needle)
                    if name_score <= 0 and not content_match:
                        continue
                    check = self.guard.check(candidate)
                    if not check.allowed:
                        continue
                    seen.add(candidate)
                    entry = self._entry(candidate)
                    scored.append((name_score if name_score > 0 else 0.5, entry))
                    results.append(entry)
        # Best name match first, then most recently modified. A user asking for
        # "the project report" wants project_report.md ahead of notes.md, even if
        # notes.md was touched more recently.
        scored.sort(
            key=lambda item: (
                item[0],
                item[1].modified_at or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return [entry for _score, entry in scored[:limit]]

    def _contains(self, path: Path, needle: str) -> bool:
        if not needle:
            return False
        try:
            if path.stat().st_size > self.guard.max_file_bytes:
                return False
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if needle in line.lower():
                        return True
        except OSError:
            return False
        return False

    async def list_directory(self, path: Path, *, include_hidden: bool = False) -> list[FileEntry]:
        return await asyncio.to_thread(self._list_sync, path, include_hidden)

    def _list_sync(self, path: Path, include_hidden: bool) -> list[FileEntry]:
        target = self.guard.resolve(path, must_exist=True)
        if not target.is_dir():
            raise NotFoundError(f"'{target.name}' is not a folder.", details={"path": str(target)})
        entries: list[FileEntry] = []
        try:
            children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            raise ToolError(f"The folder could not be listed: {exc}") from exc
        for child in children:
            if not include_hidden and child.name.startswith("."):
                continue
            if child.name in SKIP_DIRECTORIES:
                continue
            if not self.guard.check(child).allowed:
                continue
            entries.append(self._entry(child))
            if len(entries) >= self.max_results:
                break
        return entries

    async def read(self, path: Path, *, max_bytes: int | None = None) -> FileContent:
        return await asyncio.to_thread(self._read_sync, path, max_bytes)

    def _read_sync(self, path: Path, max_bytes: int | None) -> FileContent:
        target = self.guard.resolve(path, must_exist=True)
        if not self.guard.is_regular_file(target):
            raise PathNotAllowedError(
                "Only regular files can be read (not devices, sockets or pipes).",
                details={"path": str(target)},
            )
        size = self.guard.check_size(target)
        limit = min(max_bytes or self.guard.max_file_bytes, self.guard.max_file_bytes)
        try:
            with target.open("rb") as handle:
                raw = handle.read(limit + 1)
        except OSError as exc:
            raise ToolError(f"The file could not be read: {exc}") from exc
        truncated = len(raw) > limit
        raw = raw[:limit]
        if b"\x00" in raw[:8192]:
            raise ToolError(
                f"'{target.name}' looks like a binary file, so there is no text to read.",
                details={"path": str(target)},
            )
        text = raw.decode("utf-8", errors="replace")
        return FileContent(
            path=str(target), text=text, truncated=truncated, bytes_read=min(size, len(raw))
        )

    async def metadata(self, path: Path, *, hash_contents: bool = False) -> FileMetadata:
        return await asyncio.to_thread(self._metadata_sync, path, hash_contents)

    def _metadata_sync(self, path: Path, hash_contents: bool) -> FileMetadata:
        target = self.guard.resolve(path, must_exist=True)
        stat_result = target.stat()
        digest: str | None = None
        line_count: int | None = None
        word_count: int | None = None
        if target.is_file() and stat_result.st_size <= self.guard.max_file_bytes:
            if hash_contents:
                digest = _sha256(target)
            if target.suffix.lower() in TEXT_EXTENSIONS:
                line_count, word_count = _count_text(target)
        return FileMetadata(
            path=str(target),
            name=target.name,
            size_bytes=stat_result.st_size,
            created_at=_dt(getattr(stat_result, "st_birthtime", stat_result.st_ctime)),
            modified_at=_dt(stat_result.st_mtime),
            extension=target.suffix.lower(),
            mime_type=mimetypes.guess_type(target.name)[0],
            sha256=digest,
            line_count=line_count,
            word_count=word_count,
            is_symlink=target.is_symlink(),
        )

    # -- mutating operations --------------------------------------------------

    async def create(self, path: Path, content: str, *, overwrite: bool = False) -> FileMetadata:
        return await asyncio.to_thread(self._create_sync, path, content, overwrite)

    def _create_sync(self, path: Path, content: str, overwrite: bool) -> FileMetadata:
        target = self.guard.resolve(path)
        safe_name = sanitize_filename(target.name)
        target = target.parent / safe_name
        if target.exists() and not overwrite:
            raise ConflictError(
                f"'{target.name}' already exists. Ask again with overwrite to replace it.",
                details={"path": str(target)},
            )
        if not target.parent.exists():
            self.guard.resolve(target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"The file could not be written: {exc}") from exc
        return self._metadata_sync(target, False)

    async def rename(self, path: Path, new_name: str) -> FileMetadata:
        return await asyncio.to_thread(self._rename_sync, path, new_name)

    def _rename_sync(self, path: Path, new_name: str) -> FileMetadata:
        source = self.guard.resolve(path, must_exist=True)
        clean = sanitize_filename(new_name)
        if not clean:
            raise ToolError("The new name is empty after removing unsafe characters.")
        destination = self.guard.assert_within_root(source.parent / clean, source.parent)
        destination = self.guard.resolve(destination)
        if destination.exists():
            raise ConflictError(
                f"'{destination.name}' already exists.", details={"path": str(destination)}
            )
        source.rename(destination)
        return self._metadata_sync(destination, False)

    async def move(self, path: Path, destination_dir: Path) -> FileMetadata:
        return await asyncio.to_thread(self._move_sync, path, destination_dir)

    def _move_sync(self, path: Path, destination_dir: Path) -> FileMetadata:
        source = self.guard.resolve(path, must_exist=True)
        target_dir = self.guard.resolve(destination_dir, must_exist=True)
        if not target_dir.is_dir():
            raise ToolError("The destination is not a folder.", details={"path": str(target_dir)})
        destination = self.guard.resolve(target_dir / source.name)
        if destination.exists():
            raise ConflictError(
                f"'{destination.name}' already exists in the destination folder.",
                details={"path": str(destination)},
            )
        shutil.move(str(source), str(destination))
        return self._metadata_sync(destination, False)

    async def delete(self, path: Path) -> str:
        return await asyncio.to_thread(self._delete_sync, path)

    def _delete_sync(self, path: Path) -> str:
        target = self.guard.resolve(path, must_exist=True)
        if target.is_dir():
            raise PathNotAllowedError(
                "PRIVIA never deletes folders, only individual files.",
                details={"path": str(target)},
            )
        if not self.guard.is_regular_file(target):
            raise PathNotAllowedError("Only regular files can be deleted.")
        try:
            target.unlink()
        except OSError as exc:
            raise ToolError(f"The file could not be deleted: {exc}") from exc
        return str(target)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _entry(path: Path) -> FileEntry:
        try:
            stat_result = path.stat()
            size = stat_result.st_size
            modified = _dt(stat_result.st_mtime)
        except OSError:
            size, modified = 0, None
        return FileEntry(
            path=str(path),
            name=path.name,
            is_dir=path.is_dir(),
            size_bytes=size,
            modified_at=modified,
            extension=path.suffix.lower(),
            mime_type=mimetypes.guess_type(path.name)[0],
        )


_WORD_RE = re.compile(r"[a-z0-9]+")


def _name_score(filename: str, needle: str, tokens: list[str]) -> float:
    """Score how well a file name matches the query.

    Users type "project report" for a file called ``project_report.md``, so a
    plain substring test finds nothing. Separators are normalised to spaces and
    every query token must appear; exact substring matches still rank highest.
    """
    if not needle:
        return 1.0
    lowered = filename.lower()
    if needle in lowered:
        return 3.0
    flat = _WORD_RE.findall(lowered)
    if not tokens:
        return 0.0
    joined = " ".join(flat)
    if " ".join(tokens) in joined:
        return 2.5
    matched = sum(1 for token in tokens if any(token in word for word in flat))
    if matched == len(tokens):
        return 2.0
    if matched and matched >= max(1, len(tokens) - 1):
        return 1.0 + matched / (len(tokens) + 1)
    return 0.0


def _dt(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_text(path: Path) -> tuple[int, int]:
    lines = words = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                lines += 1
                words += len(line.split())
    except OSError:
        return 0, 0
    return lines, words


def summarize_text(text: str, *, max_sentences: int = 6) -> str:
    """Extractive summary used when no language model is available.

    Scores sentences by the frequency of their content words and keeps the
    highest scoring ones in their original order. Deterministic and offline.
    """
    import re

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return "The document is empty."
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 20]
    if len(sentences) <= max_sentences:
        return cleaned[:1500]
    stopwords = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "this",
        "that",
        "as",
        "at",
        "by",
        "from",
        "we",
        "our",
        "you",
        "your",
        "they",
        "their",
        "has",
        "have",
        "had",
        "will",
        "would",
    }
    frequencies: dict[str, int] = {}
    for word in re.findall(r"[a-zA-Z][a-zA-Z'-]+", cleaned.lower()):
        if word not in stopwords and len(word) > 2:
            frequencies[word] = frequencies.get(word, 0) + 1
    if not frequencies:
        return " ".join(sentences[:max_sentences])
    scored: list[tuple[int, float, str]] = []
    for index, sentence in enumerate(sentences):
        tokens = re.findall(r"[a-zA-Z][a-zA-Z'-]+", sentence.lower())
        if not tokens:
            continue
        score = sum(frequencies.get(t, 0) for t in tokens) / (len(tokens) ** 0.6)
        scored.append((index, score, sentence))
    top = sorted(scored, key=lambda item: item[1], reverse=True)[:max_sentences]
    return " ".join(sentence for _index, _score, sentence in sorted(top, key=lambda i: i[0]))


def iter_allowed(paths: Iterable[Path], guard: PathGuard) -> list[Path]:
    return [p for p in paths if guard.check(p).allowed]
