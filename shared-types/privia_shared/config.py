"""Application configuration.

Every setting has a safe, local-first default. PRIVIA must boot and be useful
with an empty environment: no API keys, no network, no models installed.

``Settings.validate_startup()`` is called during application start-up and turns
dangerous or contradictory configurations into a hard failure rather than a
silent downgrade of the security posture.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigurationError

DEFAULT_PORT = 8756
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _split_csv(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


class Settings(BaseSettings):
    """Runtime configuration, sourced from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    privia_host: str = "127.0.0.1"
    privia_port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    privia_data_dir: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,tauri://localhost"

    # --- Database ------------------------------------------------------------
    database_url: str = ""

    # --- Local AI ------------------------------------------------------------
    local_llm_provider: Literal["ollama", "heuristic"] = "ollama"
    local_llm_model: str = "llama3.1:8b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    local_embedding_provider: Literal["local", "ollama"] = "local"
    local_embedding_model: str = "nomic-embed-text"
    llm_timeout_seconds: float = Field(default=90.0, gt=0, le=600)

    # --- Cloud AI (opt-in) ---------------------------------------------------
    cloud_processing_enabled: bool = False
    cloud_llm_provider: Literal["", "openai", "anthropic"] = ""
    cloud_llm_model: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    google_client_id: str = ""
    google_client_secret: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    cloud_data_retention_days: int = Field(default=0, ge=0, le=3650)

    # --- Speech --------------------------------------------------------------
    stt_provider: Literal["faster-whisper", "disabled"] = "faster-whisper"
    stt_model: str = "base.en"
    stt_compute_type: str = "int8"
    tts_provider: Literal["pyttsx3", "disabled"] = "disabled"

    # --- Permissions ---------------------------------------------------------
    allowed_directories: str = ""
    terminal_workspace_roots: str = ""
    browser_allowed_domains: str = ""
    browser_blocked_domains: str = ""

    # --- Email ---------------------------------------------------------------
    email_provider: Literal["local", "smtp", "mock"] = "local"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    imap_host: str = ""
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username: str = ""
    imap_password: str = ""
    email_from: str = ""

    # --- Calendar ------------------------------------------------------------
    calendar_provider: Literal["local", "mock"] = "local"
    calendar_ics_dir: str = ""

    # --- Memory --------------------------------------------------------------
    memory_enabled: bool = True
    memory_max_records: int = Field(default=5000, ge=0)
    vector_store: Literal["sqlite", "chroma"] = "sqlite"

    # --- Security limits -----------------------------------------------------
    privia_api_token: str = ""
    secrets_backend: Literal["keychain", "file"] = "file"
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    max_file_read_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    max_tool_output_bytes: int = Field(default=256 * 1024, ge=1024)
    max_page_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    command_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    http_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    telemetry_enabled: bool = False

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("privia_host")
    @classmethod
    def _strip_host(cls, v: str) -> str:
        return v.strip() or "127.0.0.1"

    @model_validator(mode="after")
    def _derive_defaults(self) -> Settings:
        if not self.privia_data_dir:
            object.__setattr__(self, "privia_data_dir", str(Path.home() / ".privia"))
        if not self.database_url:
            db = Path(self.privia_data_dir) / "privia.db"
            object.__setattr__(self, "database_url", f"sqlite:///{db}")
        if not self.calendar_ics_dir:
            object.__setattr__(
                self, "calendar_ics_dir", str(Path(self.privia_data_dir) / "calendar")
            )
        return self

    # -------------------------------------------------------------------------
    # Derived accessors
    # -------------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return Path(self.privia_data_dir).expanduser()

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.json"

    @property
    def email_store_dir(self) -> Path:
        return self.data_dir / "mail"

    @property
    def calendar_dir(self) -> Path:
        return Path(self.calendar_ics_dir).expanduser()

    @property
    def database_path(self) -> Path | None:
        """Filesystem path for SQLite URLs; ``None`` for other backends."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url[len(prefix) :]
        if raw in {"", ":memory:"}:
            return None
        return Path(raw).expanduser()

    @property
    def allowed_directory_list(self) -> tuple[Path, ...]:
        return tuple(Path(p).expanduser() for p in _split_csv(self.allowed_directories))

    @property
    def terminal_root_list(self) -> tuple[Path, ...]:
        roots = _split_csv(self.terminal_workspace_roots)
        if roots:
            return tuple(Path(p).expanduser() for p in roots)
        return self.allowed_directory_list

    @property
    def browser_allowed_domain_list(self) -> tuple[str, ...]:
        return tuple(d.lower() for d in _split_csv(self.browser_allowed_domains))

    @property
    def browser_blocked_domain_list(self) -> tuple[str, ...]:
        return tuple(d.lower() for d in _split_csv(self.browser_blocked_domains))

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return _split_csv(self.cors_origins)

    @property
    def is_loopback(self) -> bool:
        return self.privia_host in LOOPBACK_HOSTS

    @property
    def cloud_api_key(self) -> str:
        if self.cloud_llm_provider == "openai":
            return self.openai_api_key
        if self.cloud_llm_provider == "anthropic":
            return self.anthropic_api_key
        return ""

    def cloud_ready(self) -> bool:
        return bool(
            self.cloud_processing_enabled and self.cloud_llm_provider and self.cloud_api_key
        )

    # -------------------------------------------------------------------------
    # Startup validation
    # -------------------------------------------------------------------------

    def validate_startup(self) -> list[str]:
        """Fail fast on unsafe configuration; return non-fatal warnings.

        Raises:
            ConfigurationError: when continuing would weaken the security model.
        """
        problems: list[str] = []
        warnings: list[str] = []

        # Binding beyond loopback without a token would expose tool execution
        # to the local network. That is never acceptable.
        if not self.is_loopback and not self.privia_api_token:
            problems.append(
                f"PRIVIA_HOST is '{self.privia_host}' (not loopback) but PRIVIA_API_TOKEN is "
                "empty. Set a token or bind to 127.0.0.1."
            )
        if self.privia_api_token and len(self.privia_api_token) < 16:
            problems.append("PRIVIA_API_TOKEN must be at least 16 characters.")

        if self.cloud_processing_enabled:
            if not self.cloud_llm_provider:
                problems.append(
                    "CLOUD_PROCESSING_ENABLED is true but CLOUD_LLM_PROVIDER is not set."
                )
            elif not self.cloud_api_key:
                problems.append(
                    f"Cloud provider '{self.cloud_llm_provider}' is enabled but its API key is "
                    "missing."
                )

        for raw in _split_csv(self.allowed_directories):
            path = Path(raw).expanduser()
            if not path.is_absolute():
                problems.append(f"ALLOWED_DIRECTORIES entry must be absolute: {raw}")
            elif str(path) == os.sep:
                problems.append("ALLOWED_DIRECTORIES must not contain the filesystem root.")
            elif not path.exists():
                warnings.append(f"Allowed directory does not exist yet: {path}")

        for raw in _split_csv(self.terminal_workspace_roots):
            path = Path(raw).expanduser()
            if not path.is_absolute():
                problems.append(f"TERMINAL_WORKSPACE_ROOTS entry must be absolute: {raw}")
            elif str(path) == os.sep:
                problems.append("TERMINAL_WORKSPACE_ROOTS must not contain the filesystem root.")

        if self.email_provider == "smtp":
            missing = [
                name
                for name, value in (
                    ("SMTP_HOST", self.smtp_host),
                    ("SMTP_USERNAME", self.smtp_username),
                    ("EMAIL_FROM", self.email_from or self.smtp_username),
                )
                if not value
            ]
            if missing:
                problems.append("EMAIL_PROVIDER=smtp requires: " + ", ".join(missing))
            if not self.smtp_starttls and self.smtp_port not in (465,):
                warnings.append(
                    "SMTP_STARTTLS is disabled on a non-implicit-TLS port; credentials would be "
                    "sent in the clear."
                )

        if self.max_tool_output_bytes > self.max_file_read_bytes:
            warnings.append(
                "MAX_TOOL_OUTPUT_BYTES exceeds MAX_FILE_READ_BYTES; file reads will be truncated "
                "by the smaller limit."
            )

        if self.app_env == "production" and self.log_level == "DEBUG":
            warnings.append("DEBUG logging in production can record sensitive request fields.")

        if self.telemetry_enabled:
            warnings.append(
                "TELEMETRY_ENABLED is true. PRIVIA ships no telemetry sink; this flag only "
                "affects local metrics retention."
            )

        if not self.allowed_directory_list:
            warnings.append(
                "No allowed directories configured. File tools will refuse every path until you "
                "grant a folder in the Privacy Center."
            )

        if problems:
            raise ConfigurationError(
                "PRIVIA cannot start with the current configuration.",
                details={"problems": problems, "warnings": warnings},
            )
        return warnings

    def ensure_directories(self) -> None:
        """Create the local data directories PRIVIA owns (never user folders)."""
        for path in (self.data_dir, self.logs_dir, self.calendar_dir, self.email_store_dir):
            path.mkdir(parents=True, exist_ok=True)
        db_path = self.database_path
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict[str, Any]:
        """A dump safe to log or return over the API."""
        secret_fields = {
            "openai_api_key",
            "anthropic_api_key",
            "google_client_secret",
            "azure_openai_api_key",
            "smtp_password",
            "imap_password",
            "privia_api_token",
        }
        out: dict[str, Any] = {}
        for key, value in self.model_dump().items():
            if key in secret_fields:
                out[key] = "***set***" if value else ""
            else:
                out[key] = value
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests and by the settings API after a configuration change."""
    get_settings.cache_clear()
