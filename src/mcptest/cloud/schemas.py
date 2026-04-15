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
    # Run labels
    git_sha: str | None = None
    git_ref: str | None = None
    branch: str | None = None
    environment: str | None = None

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
    is_baseline: bool = False

    @field_validator("is_baseline", mode="before")
    @classmethod
    def _coerce_is_baseline(cls, v: Any) -> bool:
        """Accept None from ORM rows created before this column existed."""
        if v is None:
            return False
        return v


class HealthStatus(BaseModel):
    status: str = "ok"
    service: str = "mcptest-cloud"
    version: str = "0.1.0"


class HealthReadyStatus(BaseModel):
    """Response for GET /health/ready — includes database connectivity."""

    status: str  # "ready" | "unavailable"
    db: str  # "ok" | "error: <detail>"
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


class BaselinePromoteOut(BaseModel):
    """Response body for POST/DELETE /runs/{id}/promote."""

    id: int
    suite: str | None
    is_baseline: bool
    message: str


class MetricHistoryPoint(BaseModel):
    """Single time-series data point for metric history."""

    run_id: int
    created_at: datetime
    branch: str | None
    metric_scores: dict[str, float]


class MetricHistoryOut(BaseModel):
    """Response body for GET /metrics/history."""

    points: list[MetricHistoryPoint]
    suite: str | None
    branch: str | None
    metric: str | None


class AutoCompareOut(BaseModel):
    """Response body for POST /runs/{id}/check — auto-compare against baseline."""

    # Embedded comparison result (None when no baseline)
    base_id: int | None
    head_id: int
    deltas: list[ComparisonDelta]
    overall_passed: bool
    regression_count: int
    # Extra context
    baseline_id: int | None
    baseline_branch: str | None
    status: str  # "pass" | "fail" | "no_baseline"


# ---------------------------------------------------------------------------
# Webhook schemas
# ---------------------------------------------------------------------------


class WebhookCreate(BaseModel):
    """POST /webhooks request body."""

    url: str
    secret: str | None = None
    events: list[str]
    suite_filter: str | None = None
    active: bool = True


class WebhookUpdate(BaseModel):
    """PATCH /webhooks/{id} request body — all fields optional."""

    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    suite_filter: str | None = None
    active: bool | None = None


class WebhookOut(BaseModel):
    """Webhook representation in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    secret: str | None
    events: list[str]
    suite_filter: str | None
    active: bool
    created_at: datetime


class WebhookDeliveryOut(BaseModel):
    """Webhook delivery audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    webhook_id: int
    event: str
    payload: dict[str, Any]
    response_status: int | None
    response_body: str | None
    success: bool
    attempt: int
    created_at: datetime


class WebhookTestOut(BaseModel):
    """Response body for POST /webhooks/{id}/test."""

    success: bool
    status_code: int | None
    message: str


class WebhookEventPayload(BaseModel):
    """Canonical shape POSTed to webhook URLs."""

    event: str
    timestamp: datetime
    data: dict[str, Any]
