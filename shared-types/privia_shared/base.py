"""Common Pydantic base model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PriviaModel(BaseModel):
    """Base model with PRIVIA's shared serialisation policy."""

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=False,
        validate_assignment=True,
        str_strip_whitespace=True,
        ser_json_timedelta="float",
    )


class OpenModel(BaseModel):
    """Base model for payloads coming from third parties where unknown keys are
    tolerated (for example an LLM response we will re-validate downstream)."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)
