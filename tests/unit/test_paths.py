"""PathGuard: the filesystem boundary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from privia_security.paths import PathGuard, safe_join, sanitize_filename
from privia_shared.errors import PathNotAllowedError, PathTraversalError


def test_allows_a_file_inside_an_allowed_root(workspace: Path) -> None:
    guard = PathGuard([workspace])
    decision = guard.check(workspace / "project_report.md")
    assert decision.allowed
    assert decision.root == workspace.resolve()


def test_refuses_everything_when_no_root_is_configured(workspace: Path) -> None:
    guard = PathGuard([])
    decision = guard.check(workspace / "project_report.md")
    assert not decision.allowed
    assert "Privacy Center" in decision.reason


def test_blocks_parent_traversal(workspace: Path) -> None:
    guard = PathGuard([workspace])
    assert not guard.check(f"{workspace}/../../etc/passwd").allowed
    assert not guard.check(f"{workspace}/projects/../../../etc/shadow").allowed


def test_blocks_paths_outside_the_root(workspace: Path, outside_dir: Path) -> None:
    guard = PathGuard([workspace])
    assert not guard.check(outside_dir / "secret.txt").allowed


def test_blocks_symlink_escape(workspace: Path, outside_dir: Path) -> None:
    link = workspace / "escape"
    link.symlink_to(outside_dir)
    guard = PathGuard([workspace])
    assert not guard.check(link / "secret.txt").allowed


def test_blocks_sensitive_directories_inside_an_allowed_root(workspace: Path) -> None:
    guard = PathGuard([workspace])
    decision = guard.check(workspace / ".ssh" / "id_rsa")
    assert not decision.allowed
    assert "sensitive" in decision.reason


@pytest.mark.parametrize(
    "name", [".env", ".netrc", "id_rsa", "credentials", "secrets.json", ".pgpass"]
)
def test_blocks_sensitive_file_names(workspace: Path, name: str) -> None:
    (workspace / name).write_text("x")
    guard = PathGuard([workspace])
    assert not guard.check(workspace / name).allowed


@pytest.mark.parametrize("suffix", [".pem", ".key", ".p12", ".kdbx"])
def test_blocks_key_material_by_suffix(workspace: Path, suffix: str) -> None:
    target = workspace / f"cert{suffix}"
    target.write_text("x")
    guard = PathGuard([workspace])
    assert not guard.check(target).allowed


@pytest.mark.parametrize("prefix", ["/etc", "/proc", "/sys", "/dev"])
def test_blocks_system_prefixes_even_if_allowed(prefix: str) -> None:
    guard = PathGuard([prefix])
    assert not guard.check(f"{prefix}/anything").allowed


def test_rejects_relative_and_empty_paths(workspace: Path) -> None:
    guard = PathGuard([workspace])
    assert not guard.check("relative/path.txt").allowed
    assert not guard.check("").allowed
    assert not guard.check("   ").allowed


def test_rejects_null_bytes(workspace: Path) -> None:
    guard = PathGuard([workspace])
    assert not guard.check(f"{workspace}/evil\x00.txt").allowed


def test_must_exist_flag(workspace: Path) -> None:
    guard = PathGuard([workspace])
    assert guard.check(workspace / "nope.md").allowed
    assert not guard.check(workspace / "nope.md", must_exist=True).allowed


def test_resolve_raises_with_a_useful_message(workspace: Path) -> None:
    guard = PathGuard([workspace])
    with pytest.raises(PathNotAllowedError) as caught:
        guard.resolve("/etc/passwd")
    assert caught.value.details["path"] == "/etc/passwd"


def test_size_limit(workspace: Path) -> None:
    guard = PathGuard([workspace], max_file_bytes=32)
    big = workspace / "big.txt"
    big.write_text("x" * 100)
    with pytest.raises(PathNotAllowedError) as caught:
        guard.check_size(big)
    assert caught.value.details["size_bytes"] == 100


def test_nested_roots_are_collapsed(tmp_path: Path) -> None:
    parent = tmp_path / "a"
    child = parent / "b"
    child.mkdir(parents=True)
    guard = PathGuard([child, parent])
    assert guard.roots == (parent.resolve(),)


def test_assert_within_root_blocks_escape(workspace: Path, outside_dir: Path) -> None:
    guard = PathGuard([workspace])
    with pytest.raises(PathTraversalError):
        guard.assert_within_root(outside_dir / "x.txt", workspace)


def test_safe_join(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "a", "b.txt").name == "b.txt"
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "..", "..", "etc", "passwd")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("normal.txt", "normal.txt"),
        ("../../etc/passwd", "etcpasswd"),
        ("with/slash.txt", "withslash.txt"),
        ("", "untitled"),
        ("...", "untitled"),
        ('bad:name*?".txt', "badname.txt"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_is_regular_file_rejects_fifo(tmp_path: Path) -> None:
    guard = PathGuard([tmp_path])
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError):
        pytest.skip("FIFOs are not supported here")
    assert guard.is_regular_file(fifo) is False
