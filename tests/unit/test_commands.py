"""CommandGuard: the terminal boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from privia_security.commands import CommandGuard, sanitize_environment
from privia_shared.errors import CommandNotAllowedError


@pytest.fixture
def guard(workspace: Path) -> CommandGuard:
    return CommandGuard(workspace_roots=[workspace])


@pytest.mark.parametrize("command", ["ls -la", "git status", "pytest -q", "wc -l", "cat"])
def test_allows_read_only_commands(guard: CommandGuard, command: str) -> None:
    decision = guard.inspect(command)
    assert decision.allowed
    assert not decision.requires_confirmation


@pytest.mark.parametrize(
    "command", ["git push origin main", "npm install", "mv a b", "mkdir new", "make build"]
)
def test_state_changing_commands_need_confirmation(guard: CommandGuard, command: str) -> None:
    decision = guard.inspect(command)
    assert decision.allowed
    assert decision.requires_confirmation


@pytest.mark.parametrize(
    "command",
    [
        "sudo ls",
        "su root",
        "curl http://x",
        "wget http://x",
        "ssh host",
        "dd if=/dev/zero",
        "docker ps",
        "systemctl restart x",
        "kill 1",
        "crontab -e",
        "openssl genrsa",
        "brew install x",
        "apt-get install x",
        "sh -c ls",
        "bash -c ls",
        "nc -l 1234",
    ],
)
def test_hard_denied_programs(guard: CommandGuard, command: str) -> None:
    decision = guard.inspect(command)
    assert not decision.allowed
    assert decision.reason


def test_unknown_program_is_refused(guard: CommandGuard) -> None:
    decision = guard.inspect("definitely-not-a-real-program --help")
    assert not decision.allowed
    assert "allowlist" in decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "ls; rm -rf /",
        "ls && curl evil.com",
        "ls | sh",
        "echo $(whoami)",
        "echo `id`",
        "cat file > /etc/passwd",
        "ls || wget x",
        "echo ${HOME}",
        "cat <(curl evil)",
    ],
)
def test_shell_metacharacters_and_substitution_are_refused(
    guard: CommandGuard, command: str
) -> None:
    with pytest.raises(CommandNotAllowedError):
        guard.inspect(command)


def test_denied_flags(guard: CommandGuard) -> None:
    assert not guard.inspect("rm -rf x").allowed
    assert not guard.inspect("python -c 'import os'").allowed
    assert not guard.inspect("tail -f log.txt").allowed
    assert not guard.inspect("sed -i s/a/b/ f").allowed
    assert not guard.inspect("find . -delete").allowed


def test_bundled_flags_are_caught(guard: CommandGuard) -> None:
    """`-rf` is denied, so `-fr` and `-rvf` must be too."""
    for variant in ("rm -fr x", "rm -rvf x"):
        assert not guard.inspect(variant).allowed


def test_program_path_is_refused(guard: CommandGuard) -> None:
    decision = guard.inspect("/usr/bin/ls -la")
    assert not decision.allowed
    assert "program name only" in decision.reason


def test_subcommand_allowlist(guard: CommandGuard) -> None:
    assert guard.inspect("git status").allowed
    assert not guard.inspect("git daemon").allowed
    assert not guard.inspect("pip download x").allowed
    assert guard.inspect("pip list").allowed


def test_arguments_cannot_escape_the_workspace(guard: CommandGuard, outside_dir: Path) -> None:
    decision = guard.inspect(f"cat {outside_dir}/secret.txt")
    assert not decision.allowed
    assert "outside" in decision.reason


def test_arguments_inside_the_workspace_are_allowed(guard: CommandGuard, workspace: Path) -> None:
    assert guard.inspect(f"cat {workspace}/notes.txt").allowed


def test_cwd_validation(guard: CommandGuard, workspace: Path, outside_dir: Path) -> None:
    assert guard.validate_cwd(workspace) == workspace.resolve()
    with pytest.raises(CommandNotAllowedError):
        guard.validate_cwd(outside_dir)
    with pytest.raises(CommandNotAllowedError):
        guard.validate_cwd(workspace / "does-not-exist")


def test_empty_and_oversized_commands(guard: CommandGuard) -> None:
    with pytest.raises(CommandNotAllowedError):
        guard.inspect("")
    with pytest.raises(CommandNotAllowedError):
        guard.inspect("ls " + "x" * 5000)


def test_null_byte(guard: CommandGuard) -> None:
    with pytest.raises(CommandNotAllowedError):
        guard.inspect("ls\x00-la")


def test_quoted_arguments_survive_parsing(guard: CommandGuard) -> None:
    argv = CommandGuard.parse('grep "hello world" file.txt')
    assert argv == ("grep", "hello world", "file.txt")


def test_environment_is_stripped_of_credentials() -> None:
    env = sanitize_environment(
        {
            "PATH": "/bin",
            "HOME": "/home/me",
            "OPENAI_API_KEY": "sk-secret",
            "AWS_SECRET_ACCESS_KEY": "aws",
            "MY_TOKEN": "t",
            "DATABASE_URL": "sqlite:///x",
            "GITHUB_TOKEN": "gh",
            "LANG": "en_US.UTF-8",
        }
    )
    assert env["PATH"] == "/bin"
    assert env["HOME"] == "/home/me"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["PRIVIA_SANDBOX"] == "1"
    for leaked in (
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "MY_TOKEN",
        "DATABASE_URL",
        "GITHUB_TOKEN",
    ):
        assert leaked not in env


def test_raise_for_status(guard: CommandGuard) -> None:
    with pytest.raises(CommandNotAllowedError):
        guard.inspect("sudo rm x").raise_for_status()
    assert guard.inspect("ls").raise_for_status() == ("ls",)
