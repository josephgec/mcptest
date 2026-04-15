"""Benchmark report aggregation model.

:class:`BenchmarkReport` is the top-level result object produced by a
benchmark run.  It aggregates a flat list of :class:`BenchmarkEntry` objects
into per-agent :class:`AgentSummary` records, computes a ranking, and
identifies the best-performing agent.

Typical usage::

    entries = BenchmarkRunner(profiles, test_path="tests/").run()
    report = BenchmarkReport.from_entries(entries)

    print(report.best_agent)
    for summary in report.summaries:
        print(summary.agent, summary.composite_score, summary.pass_rate)

    # Machine-readable output:
    print(report.to_json())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mcptest.bench.runner import BenchmarkEntry


@dataclass
class AgentSummary:
    """Aggregated benchmark results for one agent across all test cases.

    Attributes:
        agent: Agent profile name.
        total_cases: Number of (suite, case) pairs evaluated.
        passed_cases: Number of cases where ``trace.succeeded`` was ``True``
            (or the retry result passed).
        pass_rate: ``passed_cases / total_cases`` (0.0–1.0).
        composite_score: Mean of per-case average metric scores (0.0–1.0).
            Cases with no metrics (e.g. setup errors) are excluded.
        per_metric: Average score per metric name across all cases.
        total_duration_s: Sum of ``duration_s`` across all cases.
    """

    agent: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    composite_score: float
    per_metric: dict[str, float]
    total_duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "pass_rate": round(self.pass_rate, 4),
            "composite_score": round(self.composite_score, 4),
            "per_metric": {k: round(v, 4) for k, v in self.per_metric.items()},
            "total_duration_s": round(self.total_duration_s, 3),
        }


@dataclass
class BenchmarkReport:
    """Aggregated cross-agent comparison report.

    Attributes:
        entries: All individual case results from the run.
        summaries: One :class:`AgentSummary` per agent, sorted by
            ``composite_score`` descending.
        ranking: Agent names in the same order as *summaries*.
        best_agent: Name of the top-ranked agent (empty string if no agents).
        timestamp: ISO-8601 UTC timestamp of when the report was created.
    """

    entries: list[BenchmarkEntry]
    summaries: list[AgentSummary]
    ranking: list[str]
    best_agent: str
    timestamp: str

    @classmethod
    def from_entries(cls, entries: list[BenchmarkEntry]) -> BenchmarkReport:
        """Aggregate a flat list of :class:`BenchmarkEntry` objects.

        Groups entries by agent, computes :class:`AgentSummary` for each,
        ranks by ``composite_score`` descending (ties broken alphabetically),
        and identifies the best agent.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        if not entries:
            return cls(
                entries=[],
                summaries=[],
                ranking=[],
                best_agent="",
                timestamp=timestamp,
            )

        # Group by agent, preserving insertion order.
        by_agent: dict[str, list[BenchmarkEntry]] = {}
        for entry in entries:
            by_agent.setdefault(entry.agent, []).append(entry)

        summaries: list[AgentSummary] = [
            _build_summary(agent, agent_entries)
            for agent, agent_entries in by_agent.items()
        ]

        # Rank by composite_score descending; break ties alphabetically.
        summaries.sort(key=lambda s: (-s.composite_score, s.agent))
        ranking = [s.agent for s in summaries]
        best_agent = ranking[0] if ranking else ""

        return cls(
            entries=entries,
            summaries=summaries,
            ranking=ranking,
            best_agent=best_agent,
            timestamp=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "best_agent": self.best_agent,
            "ranking": self.ranking,
            "summaries": [s.to_dict() for s in self.summaries],
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_summary(agent: str, entries: list[BenchmarkEntry]) -> AgentSummary:
    """Build one :class:`AgentSummary` from all entries for a single agent."""
    total_cases = len(entries)
    passed_cases = sum(1 for e in entries if e.passed)
    pass_rate = passed_cases / total_cases if total_cases > 0 else 0.0
    total_duration_s = sum(e.duration_s for e in entries)

    # Per-metric averages across all cases that have metric data.
    metric_totals: dict[str, list[float]] = {}
    for entry in entries:
        for m in entry.metric_results:
            metric_totals.setdefault(m.name, []).append(m.score)
    per_metric = {
        name: sum(scores) / len(scores)
        for name, scores in metric_totals.items()
    }

    # Composite score: mean of per-case average metric scores.
    # Cases with no metric_results (e.g. setup/load errors) are excluded.
    case_scores: list[float] = []
    for entry in entries:
        if entry.metric_results:
            avg = sum(m.score for m in entry.metric_results) / len(entry.metric_results)
            case_scores.append(avg)
    composite_score = sum(case_scores) / len(case_scores) if case_scores else 0.0

    return AgentSummary(
        agent=agent,
        total_cases=total_cases,
        passed_cases=passed_cases,
        pass_rate=pass_rate,
        composite_score=composite_score,
        per_metric=per_metric,
        total_duration_s=total_duration_s,
    )
