"""Pydantic DTOs for the cloud API request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TestRunBase(BaseModel):
    trace_id: str = Field(..., min_length=1, max_length=64)
    suite: str | None = None
    case: str | None = None
    input: str = ""
    output: str = ""
    exit_code: int = 0
    duration_s: float = 0.0
    total_tool_calls: int = 0
    passed: bool = True
    agent_error: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    run_metadata: dict[str, Any] = Field(default_factory=dict)
    metric_scores: dict[str, float] = Field(default_factory=dict)

    @field_validator("metric_scores", mode="before")
    @classmethod
    def _coerce_metric_scores(cls, v: Any) -> dict[str, float]:
        """Accept None from ORM rows that were created before this column existed."""
        if v is None:
            return {}
        return v


class TestRunCreate(TestRunBase):
    """POST /runs request body."""


class TestRunOut(TestRunBase):
    """GET/POST response body."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class HealthStatus(BaseModel):
    status: str = "ok"
    service: str = "mcptest-cloud"
    version: str = "0.1.0"


class ComparisonDelta(BaseModel):
    """Per-metric delta between a base and head run."""

    name: str
    label: str
    base_score: float
    head_score: float
    delta: float
    regressed: bool


class ComparisonOut(BaseModel):
    """Response body for POST /compare."""

    base_id: int
    head_id: int
    deltas: list[ComparisonDelta]
    overall_passed: bool
    regression_count: int


class CompareRequest(BaseModel):
    """Request body for POST /compare."""

    base_id: int
    head_id: int
    thresholds: dict[str, float] | None = None
