"""Trace diffing engine and baseline snapshot storage."""

from __future__ import annotations

from mcptest.diff.baseline import BaselineStore, baseline_id
from mcptest.diff.engine import (
    Regression,
    RegressionKind,
    TraceDiff,
    diff_traces,
)

__all__ = [
    "BaselineStore",
    "Regression",
    "RegressionKind",
    "TraceDiff",
    "baseline_id",
    "diff_traces",
]
