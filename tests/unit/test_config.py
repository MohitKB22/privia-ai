"""Configuration validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from privia_shared.config import Settings, get_settings, reset_settings_cache
from privia_shared.errors import ConfigurationError


def test_boots_with_an_empty_environment() -> None:
    """PRIVIA must be usable with no configuration at all."""
    settings = Settings(_env_file=None)
    warnings = settings.validate_startup()
    assert settings.is_loopback
    assert settings.cloud_processing_enabled is False
    assert settings.telemetry_enabled is False
    assert any("No allowed directories" in w for w in warnings)


def test_non_loopback_bind_without_token_is_fatal() -> None:
    with pytest.raises(ConfigurationError) as caught:
        Settings(_env_file=None, privia_host="0.0.0.0").validate_startup()
    problems = caught.value.details["problems"]
    assert any("PRIVIA_API_TOKEN" in problem for problem in problems)


def test_non_loopback_bind_with_strong_token_is_allowed() -> None:
    settings = Settings(_env_file=None, privia_host="192.168.1.10", privia_api_token="x" * 32)
    settings.validate_startup()


def test_short_token_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Settings(_env_file=None, privia_api_token="short").validate_startup()


def test_cloud_enabled_without_provider_is_fatal() -> None:
    with pytest.raises(ConfigurationError) as caught:
        Settings(_env_file=None, cloud_processing_enabled=True).validate_startup()
    assert "CLOUD_LLM_PROVIDER" in " ".join(caught.value.details["problems"])


def test_cloud_enabled_without_key_is_fatal() -> None:
    with pytest.raises(ConfigurationError):
        Settings(
            _env_file=None, cloud_processing_enabled=True, cloud_llm_provider="openai"
        ).validate_startup()


def test_cloud_ready_requires_all_three() -> None:
    settings = Settings(
        _env_file=None,
        cloud_processing_enabled=True,
        cloud_llm_provider="openai",
        openai_api_key="sk-test",
    )
    settings.validate_startup()
    assert settings.cloud_ready() is True
    assert Settings(_env_file=None).cloud_ready() is False


def test_filesystem_root_cannot_be_allowed() -> None:
    with pytest.raises(ConfigurationError):
        Settings(_env_file=None, allowed_directories="/").validate_startup()


def test_relative_allowed_directory_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        Settings(_env_file=None, allowed_directories="documents").validate_startup()


def test_smtp_requires_credentials() -> None:
    with pytest.raises(ConfigurationError) as caught:
        Settings(_env_file=None, email_provider="smtp").validate_startup()
    assert "SMTP_HOST" in " ".join(caught.value.details["problems"])


def test_secrets_are_redacted_in_dumps() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-verysecret", smtp_password="hunter2")
    dumped = settings.redacted()
    assert dumped["openai_api_key"] == "***set***"
    assert dumped["smtp_password"] == "***set***"
    assert "sk-verysecret" not in str(dumped)
    assert "hunter2" not in str(dumped)


def test_csv_parsing(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, allowed_directories=f"{tmp_path}/a, {tmp_path}/b ,,{tmp_path}/c"
    )
    assert len(settings.allowed_directory_list) == 3


def test_terminal_roots_default_to_allowed_directories(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, allowed_directories=str(tmp_path))
    assert settings.terminal_root_list == settings.allowed_directory_list


def test_derived_paths(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, privia_data_dir=str(tmp_path / "d"))
    assert settings.database_url.startswith("sqlite:///")
    assert settings.database_path is not None
    assert settings.logs_dir.name == "logs"
    settings.ensure_directories()
    assert settings.data_dir.is_dir()
    assert settings.calendar_dir.is_dir()


def test_settings_singleton_is_cached() -> None:
    reset_settings_cache()
    assert get_settings() is get_settings()
    reset_settings_cache()
