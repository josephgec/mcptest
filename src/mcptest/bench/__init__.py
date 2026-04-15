"""mcptest bench — multi-agent benchmarking subsystem.

Run the same test suite against multiple agents and get a side-by-side
quality comparison::

    mcptest bench tests/ --agents agents.yaml
    mcptest bench tests/ --json | jq .best_agent

Programmatic usage::

    from mcptest.bench import AgentProfile, BenchmarkRunner, BenchmarkReport

    profiles = [
        AgentProfile(name="claude", command="python agents/claude.py"),
        AgentProfile(name="gpt4o", command="python agents/gpt4o.py"),
    ]
    runner = BenchmarkRunner(profiles=profiles, test_path="tests/")
    entries = runner.run()
    report = BenchmarkReport.from_entries(entries)
    print(report.best_agent, report.summaries[0].composite_score)
"""

from __future__ import annotations

from mcptest.bench.profile import AgentProfile, load_profiles, load_profiles_from_config
from mcptest.bench.report import AgentSummary, BenchmarkReport
from mcptest.bench.runner import BenchmarkEntry, BenchmarkRunner

__all__ = [
    "AgentProfile",
    "AgentSummary",
    "BenchmarkEntry",
    "BenchmarkReport",
    "BenchmarkRunner",
    "load_profiles",
    "load_profiles_from_config",
]
