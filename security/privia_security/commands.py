"""Terminal command safety.

PRIVIA never builds a shell string. A command is parsed into an argument vector,
matched against a declarative allowlist, and executed with ``shell=False``.

Three outcomes are possible:

``ALLOW``
    The program and its arguments match an allowlist rule.
``CONFIRM``
    The program is known but the specific invocation is destructive or
    state-changing; the user must approve the exact argv first.
``DENY``
    The program is not on the allowlist, or the invocation contains shell
    metacharacters, redirections, or an argument that escapes the workspace.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from privia_shared.domain import CommandInspection
from privia_shared.errors import CommandNotAllowedError

#: Characters that only have meaning to a shell. Their presence means the caller
#: is trying to do something the argv interface cannot express.
SHELL_METACHARACTERS = frozenset({"|", "&", ";", "$", "`", ">", "<", "\n", "\r", "\\", "!"})

#: Substrings that indicate command substitution even after quoting.
SUBSTITUTION_PATTERNS = ("$(", "${", "`", "<(", ">(", "&&", "||", ";;")

#: Environment variables that are never passed to a child process.
STRIPPED_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "OPENAI_",
    "ANTHROPIC_",
    "GITHUB_",
    "GITLAB_",
    "NPM_TOKEN",
    "PYPI_",
    "DOCKER_",
    "KUBE",
    "SSH_",
    "GPG_",
    "PRIVIA_API_TOKEN",
    "SMTP_",
    "IMAP_",
    "DATABASE_URL",
)

#: A minimal, predictable environment handed to every child process.
BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TERM", "USER", "SHELL", "TMPDIR")


@dataclass(frozen=True)
class CommandRule:
    """One allowlist entry."""

    program: str
    description: str
    #: When non-empty, the first argument must be one of these subcommands.
    subcommands: frozenset[str] = field(default_factory=frozenset)
    #: Subcommands that mutate state and therefore need confirmation.
    confirm_subcommands: frozenset[str] = field(default_factory=frozenset)
    #: Flags that flip an otherwise safe command into a destructive one.
    confirm_flags: frozenset[str] = field(default_factory=frozenset)
    #: Flags that are never allowed at all.
    denied_flags: frozenset[str] = field(default_factory=frozenset)
    #: The whole program always needs confirmation.
    always_confirm: bool = False
    max_args: int = 40


def _rules() -> dict[str, CommandRule]:
    rules = [
        CommandRule("ls", "List directory contents"),
        CommandRule("pwd", "Print working directory"),
        CommandRule("cat", "Print a file"),
        CommandRule("head", "First lines of a file"),
        CommandRule("tail", "Last lines of a file", denied_flags=frozenset({"-f", "--follow"})),
        CommandRule("wc", "Count lines, words and bytes"),
        CommandRule("file", "Identify a file type"),
        CommandRule("stat", "File metadata"),
        CommandRule("du", "Disk usage"),
        CommandRule("df", "Free disk space"),
        CommandRule("date", "Current date and time"),
        CommandRule("echo", "Print text"),
        CommandRule("which", "Locate a program"),
        CommandRule("env", "Show the sanitised environment"),
        CommandRule("uname", "Kernel information"),
        CommandRule("grep", "Search file contents", denied_flags=frozenset({"-r--devices"})),
        CommandRule("rg", "ripgrep search"),
        CommandRule(
            "find", "Find files", denied_flags=frozenset({"-delete", "-exec", "-execdir", "-ok"})
        ),
        CommandRule("sort", "Sort lines"),
        CommandRule("uniq", "Filter duplicate lines"),
        CommandRule("diff", "Compare files"),
        CommandRule("tree", "Directory tree"),
        CommandRule("sed", "Stream editor", denied_flags=frozenset({"-i", "--in-place"})),
        CommandRule("awk", "Pattern scanning"),
        CommandRule("jq", "Query JSON"),
        CommandRule("md5sum", "Checksum"),
        CommandRule("sha256sum", "Checksum"),
        CommandRule(
            "python",
            "Run Python",
            denied_flags=frozenset({"-c"}),
            always_confirm=True,
        ),
        CommandRule(
            "python3",
            "Run Python",
            denied_flags=frozenset({"-c"}),
            always_confirm=True,
        ),
        CommandRule(
            "pytest",
            "Run the Python test suite",
            denied_flags=frozenset({"--pdb", "-s--capture=no"}),
        ),
        CommandRule("ruff", "Python linter"),
        CommandRule("black", "Python formatter", confirm_flags=frozenset({"--check-off"})),
        CommandRule("mypy", "Python type checker"),
        CommandRule(
            "pip",
            "Python package manager",
            subcommands=frozenset({"list", "show", "freeze", "check", "install", "uninstall"}),
            confirm_subcommands=frozenset({"install", "uninstall"}),
        ),
        CommandRule(
            "npm",
            "Node package manager",
            subcommands=frozenset(
                {"run", "test", "ls", "list", "outdated", "audit", "install", "ci", "uninstall"}
            ),
            confirm_subcommands=frozenset({"install", "ci", "uninstall"}),
        ),
        CommandRule(
            "pnpm",
            "Node package manager",
            subcommands=frozenset({"run", "test", "list", "install", "audit"}),
            confirm_subcommands=frozenset({"install"}),
        ),
        CommandRule("node", "Run Node", always_confirm=True),
        CommandRule(
            "git",
            "Version control",
            subcommands=frozenset(
                {
                    "status",
                    "log",
                    "diff",
                    "show",
                    "branch",
                    "remote",
                    "config",
                    "rev-parse",
                    "describe",
                    "blame",
                    "stash",
                    "add",
                    "commit",
                    "checkout",
                    "switch",
                    "restore",
                    "fetch",
                    "pull",
                    "push",
                    "reset",
                    "clean",
                    "merge",
                    "rebase",
                    "tag",
                }
            ),
            confirm_subcommands=frozenset(
                {
                    "add",
                    "commit",
                    "checkout",
                    "switch",
                    "restore",
                    "fetch",
                    "pull",
                    "push",
                    "reset",
                    "clean",
                    "merge",
                    "rebase",
                    "tag",
                    "stash",
                }
            ),
            denied_flags=frozenset({"--upload-pack", "--receive-pack", "-c"}),
        ),
        CommandRule("make", "Run a Makefile target", always_confirm=True),
        CommandRule(
            "cargo",
            "Rust toolchain",
            subcommands=frozenset({"test", "build", "check", "fmt", "clippy"}),
            confirm_subcommands=frozenset({"build"}),
        ),
        CommandRule(
            "go",
            "Go toolchain",
            subcommands=frozenset({"test", "build", "vet", "fmt", "version"}),
            confirm_subcommands=frozenset({"build"}),
        ),
        CommandRule("mkdir", "Create a directory", always_confirm=True),
        CommandRule("cp", "Copy files", always_confirm=True, denied_flags=frozenset({"--parents"})),
        CommandRule("mv", "Move or rename", always_confirm=True),
        CommandRule("touch", "Create an empty file", always_confirm=True),
        # Denied flags are listed as individual short options so that bundles
        # (-rf, -fr, -rvf, -Rf ...) are all caught by _is_bundled_denied.
        CommandRule(
            "rm",
            "Delete files",
            always_confirm=True,
            denied_flags=frozenset(
                {"-r", "-R", "-f", "--recursive", "--force", "--no-preserve-root"}
            ),
        ),
        CommandRule(
            "chmod",
            "Change permissions",
            always_confirm=True,
            denied_flags=frozenset({"-R", "--recursive"}),
        ),
    ]
    return {rule.program: rule for rule in rules}


ALLOWLIST: dict[str, CommandRule] = _rules()

#: Programs that are refused outright with a specific explanation.
HARD_DENIED: dict[str, str] = {
    "sudo": "PRIVIA never escalates privileges.",
    "su": "PRIVIA never switches user.",
    "doas": "PRIVIA never escalates privileges.",
    "sh": "A shell would bypass the argument allowlist.",
    "bash": "A shell would bypass the argument allowlist.",
    "zsh": "A shell would bypass the argument allowlist.",
    "fish": "A shell would bypass the argument allowlist.",
    "csh": "A shell would bypass the argument allowlist.",
    "powershell": "A shell would bypass the argument allowlist.",
    "cmd": "A shell would bypass the argument allowlist.",
    "eval": "Dynamic evaluation is never allowed.",
    "exec": "Process replacement is never allowed.",
    "curl": "Network fetches must go through the browser tool, which validates URLs.",
    "wget": "Network fetches must go through the browser tool, which validates URLs.",
    "nc": "Raw network access is not available to the assistant.",
    "ncat": "Raw network access is not available to the assistant.",
    "telnet": "Raw network access is not available to the assistant.",
    "ssh": "Remote access is not available to the assistant.",
    "scp": "Remote file transfer is not available to the assistant.",
    "rsync": "Remote file transfer is not available to the assistant.",
    "ftp": "Remote file transfer is not available to the assistant.",
    "dd": "Raw device writes can destroy a disk.",
    "mkfs": "Formatting a filesystem is never allowed.",
    "fdisk": "Partitioning a disk is never allowed.",
    "mount": "Mounting filesystems is never allowed.",
    "umount": "Unmounting filesystems is never allowed.",
    "kill": "Process termination must be done by you, not the assistant.",
    "killall": "Process termination must be done by you, not the assistant.",
    "pkill": "Process termination must be done by you, not the assistant.",
    "shutdown": "Power control is never allowed.",
    "reboot": "Power control is never allowed.",
    "halt": "Power control is never allowed.",
    "systemctl": "Service control is never allowed.",
    "launchctl": "Service control is never allowed.",
    "crontab": "Scheduling background jobs is never allowed.",
    "at": "Scheduling background jobs is never allowed.",
    "defaults": "System configuration changes are never allowed.",
    "security": "Keychain access is never allowed.",
    "keychain": "Keychain access is never allowed.",
    "openssl": "Key material handling is never allowed.",
    "gpg": "Key material handling is never allowed.",
    "docker": "Container control can escape every sandbox boundary.",
    "kubectl": "Cluster control is never allowed.",
    "brew": "Package installation must be done by you, not the assistant.",
    "apt": "Package installation must be done by you, not the assistant.",
    "apt-get": "Package installation must be done by you, not the assistant.",
    "yum": "Package installation must be done by you, not the assistant.",
    "dnf": "Package installation must be done by you, not the assistant.",
    "pacman": "Package installation must be done by you, not the assistant.",
}


@dataclass(frozen=True)
class CommandDecision:
    argv: tuple[str, ...]
    program: str
    allowed: bool
    requires_confirmation: bool
    reason: str = ""
    matched_rule: str | None = None

    def to_inspection(self, raw: str) -> CommandInspection:
        return CommandInspection(
            raw=raw,
            argv=self.argv,
            program=self.program,
            allowed=self.allowed,
            requires_confirmation=self.requires_confirmation,
            reason=self.reason,
            matched_rule=self.matched_rule,
        )

    def raise_for_status(self) -> tuple[str, ...]:
        if not self.allowed:
            raise CommandNotAllowedError(
                self.reason, details={"program": self.program, "argv": list(self.argv)}
            )
        return self.argv


class CommandGuard:
    """Parses and authorises terminal commands."""

    def __init__(
        self,
        *,
        allowlist: dict[str, CommandRule] | None = None,
        extra_denied: Iterable[str] = (),
        workspace_roots: Sequence[Path] = (),
    ) -> None:
        self.allowlist = dict(allowlist or ALLOWLIST)
        self.denied = dict(HARD_DENIED)
        for program in extra_denied:
            self.denied[program] = "Blocked by local configuration."
        self.workspace_roots = tuple(
            Path(r).expanduser().resolve(strict=False) for r in workspace_roots
        )

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def parse(raw: str) -> tuple[str, ...]:
        """Turn a command string into argv, rejecting anything shell-only."""
        text = raw.strip()
        if not text:
            raise CommandNotAllowedError("The command is empty.")
        if "\x00" in text:
            raise CommandNotAllowedError("The command contains a null byte.")
        if len(text) > 4000:
            raise CommandNotAllowedError("The command is too long to be reviewed safely.")
        for pattern in SUBSTITUTION_PATTERNS:
            if pattern in text:
                raise CommandNotAllowedError(
                    f"'{pattern}' is shell substitution, which PRIVIA never evaluates.",
                    details={"pattern": pattern},
                )
        try:
            argv = shlex.split(text, comments=False, posix=True)
        except ValueError as exc:
            raise CommandNotAllowedError(f"The command could not be parsed: {exc}") from exc
        if not argv:
            raise CommandNotAllowedError("The command is empty.")
        for token in argv:
            bad = SHELL_METACHARACTERS.intersection(token)
            if bad:
                raise CommandNotAllowedError(
                    f"Argument {token!r} contains shell metacharacters: {''.join(sorted(bad))}",
                    details={"argument": token},
                )
        return tuple(argv)

    # -- authorisation -------------------------------------------------------

    def inspect(self, raw: str | Sequence[str]) -> CommandDecision:
        argv = tuple(raw) if not isinstance(raw, str) else self.parse(raw)
        program_path = argv[0]
        program = Path(program_path).name.lower()
        if program.endswith(".exe"):
            program = program[:-4]

        if "/" in program_path or "\\" in program_path:
            # An absolute or relative program path could point at a shim that
            # bypasses the allowlist. Only bare program names are accepted.
            return CommandDecision(
                argv,
                program,
                False,
                False,
                "Give the program name only; PRIVIA resolves it on PATH itself.",
            )

        if program in self.denied:
            return CommandDecision(argv, program, False, False, self.denied[program], program)

        rule = self.allowlist.get(program)
        if rule is None:
            return CommandDecision(
                argv,
                program,
                False,
                False,
                f"'{program}' is not on the command allowlist.",
            )

        args = argv[1:]
        if len(args) > rule.max_args:
            return CommandDecision(
                argv,
                program,
                False,
                False,
                f"Too many arguments for '{program}' ({len(args)} > {rule.max_args}).",
                program,
            )

        flags = [a for a in args if a.startswith("-")]
        for flag in flags:
            normalised = flag.split("=", 1)[0]
            if normalised in rule.denied_flags or flag in rule.denied_flags:
                return CommandDecision(
                    argv,
                    program,
                    False,
                    False,
                    f"'{flag}' is not permitted for '{program}'.",
                    program,
                )
            if _is_bundled_denied(flag, rule.denied_flags):
                return CommandDecision(
                    argv,
                    program,
                    False,
                    False,
                    f"'{flag}' bundles a flag that is not permitted for '{program}'.",
                    program,
                )

        positional = [a for a in args if not a.startswith("-")]
        subcommand = positional[0] if positional else ""

        if rule.subcommands:
            if not subcommand:
                return CommandDecision(
                    argv,
                    program,
                    False,
                    False,
                    f"'{program}' requires a subcommand ({', '.join(sorted(rule.subcommands))}).",
                    program,
                )
            if subcommand not in rule.subcommands:
                return CommandDecision(
                    argv,
                    program,
                    False,
                    False,
                    f"'{program} {subcommand}' is not on the allowlist.",
                    program,
                )

        requires_confirmation = bool(
            rule.always_confirm
            or (subcommand and subcommand in rule.confirm_subcommands)
            or any(f in rule.confirm_flags for f in flags)
        )

        escaping = self._escaping_arguments(args)
        if escaping:
            return CommandDecision(
                argv,
                program,
                False,
                False,
                f"Argument {escaping!r} points outside the allowed workspace.",
                program,
            )

        reason = (
            f"'{program}' changes state and needs your approval."
            if requires_confirmation
            else f"'{program}' is read-only and allowed."
        )
        return CommandDecision(argv, program, True, requires_confirmation, reason, program)

    def _escaping_arguments(self, args: Sequence[str]) -> str | None:
        """Reject absolute paths that leave every configured workspace root."""
        if not self.workspace_roots:
            return None
        for arg in args:
            if arg.startswith("-") or not arg:
                continue
            looks_like_path = arg.startswith(("/", "~")) or arg.startswith("..")
            if not looks_like_path:
                continue
            candidate = Path(arg).expanduser()
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError):
                return arg
            if not any(_within(resolved, root) for root in self.workspace_roots):
                return arg
        return None

    def validate_cwd(self, cwd: Path | str) -> Path:
        resolved = Path(cwd).expanduser().resolve(strict=False)
        if not self.workspace_roots:
            raise CommandNotAllowedError(
                "No workspace folders are configured for terminal commands."
            )
        if not any(_within(resolved, root) for root in self.workspace_roots):
            raise CommandNotAllowedError(
                "The working directory is outside every allowed workspace.",
                details={"cwd": str(resolved)},
            )
        if not resolved.is_dir():
            raise CommandNotAllowedError(
                "The working directory does not exist.", details={"cwd": str(resolved)}
            )
        return resolved


def sanitize_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Build a minimal environment with every credential-shaped variable removed."""
    import os

    source = dict(base if base is not None else os.environ)
    env: dict[str, str] = {}
    for key in BASE_ENV_KEYS:
        value = source.get(key)
        if value:
            env[key] = value
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PRIVIA_SANDBOX"] = "1"
    # Explicitly drop anything credential shaped that slipped through.
    for key in list(env):
        upper = key.upper()
        if any(upper.startswith(prefix) for prefix in STRIPPED_ENV_PREFIXES) or any(
            word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "APIKEY", "API_KEY")
        ):
            env.pop(key, None)
    return env


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


_BUNDLE_RE = re.compile(r"^-[a-zA-Z]{2,}$")


def _is_bundled_denied(flag: str, denied: frozenset[str]) -> bool:
    """Catch bundled short options such as ``-rf``, ``-fr`` and ``-rvf``.

    POSIX lets ``-r -f`` be written ``-rf`` in any order and mixed with
    unrelated letters. Comparing the whole token against the denylist would let
    ``rm -rvf`` through, so every letter in a bundle is checked individually.
    Multi-letter denied entries are also expanded, so listing ``-rf`` still
    blocks ``-fr``.
    """
    if not _BUNDLE_RE.match(flag):
        return False
    letters = set(flag[1:])
    for denied_flag in denied:
        if not denied_flag.startswith("-") or denied_flag.startswith("--"):
            continue
        if letters & set(denied_flag[1:]):
            return True
    return False
