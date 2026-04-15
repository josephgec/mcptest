"""Self-contained HTML report exporter for mcptest results.

Produces a single HTML file with inline CSS and JS — no external
dependencies, works offline, and uploads cleanly as a CI artifact.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mcptest.exporters.base import Exporter, register_exporter

try:
    from importlib.metadata import version as _pkg_version

    _VERSION = _pkg_version("mcptest")
except Exception:
    _VERSION = "dev"


def _escape_html(text: str) -> str:
    """Escape *text* for safe embedding in HTML content or attribute values."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _metric_color_class(score: float) -> str:
    if score >= 0.8:
        return "metric-good"
    if score >= 0.5:
        return "metric-warn"
    return "metric-bad"


_CSS = """\
:root {
  --bg: #f8f9fa;
  --surface: #ffffff;
  --border: #dee2e6;
  --text: #212529;
  --text-muted: #6c757d;
  --pass: #198754;
  --pass-bg: #d1e7dd;
  --fail: #dc3545;
  --fail-bg: #f8d7da;
  --warn: #fd7e14;
  --warn-bg: #ffe5d0;
  --metric-good: #198754;
  --metric-warn: #fd7e14;
  --metric-bad: #dc3545;
  --code-bg: #f1f3f5;
  --radius: 6px;
  --shadow: 0 1px 3px rgba(0,0,0,.08);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --pass: #3fb950;
    --pass-bg: #12261e;
    --fail: #f85149;
    --fail-bg: #2d1216;
    --warn: #d29922;
    --warn-bg: #2d1f03;
    --code-bg: #1f242a;
  }
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
}
a { color: inherit; text-decoration: none; }
.container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }

/* ── Header ── */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 8px;
}
.header h1 { font-size: 1.4rem; font-weight: 700; }
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .3px;
  background: var(--border);
  color: var(--text-muted);
}
.badge-version { background: #6f42c1; color: #fff; }
.header-meta { color: var(--text-muted); font-size: 12px; }

/* ── Summary bar ── */
.summary {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 24px;
}
.stat-card {
  flex: 1 1 120px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 18px;
  box-shadow: var(--shadow);
  text-align: center;
}
.stat-card .num { font-size: 2rem; font-weight: 700; }
.stat-card .lbl { font-size: 11px; text-transform: uppercase;
  letter-spacing: .5px; color: var(--text-muted); }
.stat-pass .num { color: var(--pass); }
.stat-fail .num { color: var(--fail); }
.stat-warn .num { color: var(--warn); }

/* ── Metric overview ── */
.section { margin-bottom: 24px; }
.section-title {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .5px;
  color: var(--text-muted);
  margin-bottom: 10px;
}
.metric-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.metric-pill {
  flex: 1 1 180px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 10px 14px;
  box-shadow: var(--shadow);
}
.metric-pill .metric-name {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .3px;
}
.bar-track {
  height: 8px;
  background: var(--border);
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 4px;
}
.bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width .3s;
}
.metric-good .bar-fill { background: var(--metric-good); }
.metric-warn .bar-fill { background: var(--metric-warn); }
.metric-bad  .bar-fill { background: var(--metric-bad); }
.metric-score-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}
.metric-good .score-val { color: var(--metric-good); font-weight: 700; }
.metric-warn .score-val { color: var(--metric-warn); font-weight: 700; }
.metric-bad  .score-val { color: var(--metric-bad);  font-weight: 700; }
.score-lbl { color: var(--text-muted); }

/* ── Filters ── */
.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
  align-items: center;
}
.filter-btn {
  padding: 4px 12px;
  border-radius: 20px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
}
.filter-btn.active { background: var(--text); color: var(--bg); }
.filter-count { color: var(--text-muted); font-size: 12px; margin-left: auto; }

/* ── Table ── */
.results-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow);
}
.results-table th {
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  padding: 8px 12px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .4px;
  color: var(--text-muted);
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
}
.results-table th:hover { color: var(--text); }
.results-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
.results-table tr:last-child td { border-bottom: none; }
.results-table tr.case-row { cursor: pointer; }
.results-table tr.case-row:hover td { background: var(--bg); }

/* ── Status pill ── */
.pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}
.pill-pass { background: var(--pass-bg); color: var(--pass); }
.pill-fail { background: var(--fail-bg); color: var(--fail); }
.pill-error { background: var(--warn-bg); color: var(--warn); }

/* ── Case name & suite ── */
.case-name { font-weight: 600; }
.suite-tag {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}
.expand-icon { float: right; color: var(--text-muted); font-size: 10px; }

/* ── Detail panel ── */
.detail-row { display: none; }
.detail-row.open { display: table-row; }
.detail-cell {
  padding: 0 12px 16px !important;
  border-bottom: 2px solid var(--border) !important;
}
.detail-inner { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 700px) { .detail-inner { grid-template-columns: 1fr; } }

.detail-section { background: var(--bg); border-radius: var(--radius);
  border: 1px solid var(--border); padding: 12px; }
.detail-section h4 { font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .4px; color: var(--text-muted); margin-bottom: 10px; }

/* assertions */
.assertion { display: flex; gap: 8px; margin-bottom: 6px; align-items: flex-start; }
.assertion-icon { flex-shrink: 0; font-size: 13px; margin-top: 1px; }
.assertion-name { font-size: 12px; font-weight: 600; }
.assertion-msg { font-size: 12px; color: var(--text-muted); }
.assertion-details {
  font-size: 11px;
  font-family: "SFMono-Regular", Consolas, monospace;
  background: var(--code-bg);
  border-radius: 4px;
  padding: 4px 8px;
  margin-top: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}

/* tool calls */
.tool-call { border-left: 3px solid var(--border); padding-left: 10px;
  margin-bottom: 10px; }
.tool-call.tool-error { border-color: var(--fail); }
.tool-header { display: flex; gap: 6px; align-items: center; margin-bottom: 4px; }
.tool-name { font-size: 12px; font-weight: 700; font-family: monospace; }
.tool-server { font-size: 11px; color: var(--text-muted); }
.tool-latency { font-size: 11px; color: var(--text-muted); margin-left: auto; }
.tool-args, .tool-result {
  font-size: 11px;
  font-family: "SFMono-Regular", Consolas, monospace;
  background: var(--code-bg);
  border-radius: 4px;
  padding: 4px 8px;
  margin-top: 3px;
  white-space: pre-wrap;
  word-break: break-all;
}
.tool-error-msg { font-size: 12px; color: var(--fail); font-weight: 600;
  margin-top: 3px; }

/* per-case metrics */
.case-metric-row { display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; }
.case-metric-name { font-size: 11px; font-weight: 600; color: var(--text-muted);
  width: 140px; flex-shrink: 0; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.case-metric-bar { flex: 1; }
.case-metric-score { font-size: 11px; font-weight: 700; width: 40px;
  text-align: right; }

/* ── Footer ── */
.footer {
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  text-align: center;
  font-size: 11px;
  color: var(--text-muted);
}
"""

_JS = """\
(function() {
  // ---------- expand/collapse ----------
  document.querySelectorAll('.case-row').forEach(function(row) {
    row.addEventListener('click', function() {
      var id = row.dataset.case;
      var detail = document.getElementById('detail-' + id);
      if (!detail) return;
      var icon = row.querySelector('.expand-icon');
      if (detail.classList.contains('open')) {
        detail.classList.remove('open');
        if (icon) icon.textContent = '▶';
      } else {
        detail.classList.add('open');
        if (icon) icon.textContent = '▼';
      }
    });
  });

  // ---------- filter buttons ----------
  var filterBtns = document.querySelectorAll('.filter-btn');
  filterBtns.forEach(function(btn) {
    btn.addEventListener('click', function() {
      filterBtns.forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      var filter = btn.dataset.filter;
      document.querySelectorAll('.case-row, .detail-row').forEach(function(row) {
        if (filter === 'all') {
          row.style.display = '';
        } else if (row.classList.contains('case-row')) {
          var status = row.dataset.status;
          row.style.display = (status === filter) ? '' : 'none';
          // also hide its paired detail row
          var id = row.dataset.case;
          var det = document.getElementById('detail-' + id);
          if (det) det.style.display = (status === filter) ? '' : 'none';
        }
      });
    });
  });

  // ---------- column sort ----------
  document.querySelectorAll('th[data-sort]').forEach(function(th) {
    th.addEventListener('click', function() {
      var col = th.dataset.sort;
      var tbody = document.querySelector('#results-body');
      var rows = Array.from(tbody.querySelectorAll('.case-row'));
      var asc = th.dataset.asc !== 'true';
      th.dataset.asc = asc ? 'true' : 'false';
      rows.sort(function(a, b) {
        var av = a.dataset[col] || '';
        var bv = b.dataset[col] || '';
        // numeric sort for duration and score
        if (col === 'duration' || col === 'score') {
          return asc ? parseFloat(av) - parseFloat(bv)
                     : parseFloat(bv) - parseFloat(av);
        }
        return asc ? av.localeCompare(bv) : bv.localeCompare(av);
      });
      rows.forEach(function(row) {
        var id = row.dataset.case;
        var det = document.getElementById('detail-' + id);
        tbody.appendChild(row);
        if (det) tbody.appendChild(det);
      });
    });
  });
})();
"""


@register_exporter("html")
class HtmlExporter(Exporter):
    """Exports mcptest results as a self-contained HTML report."""

    def export(self, results: list[Any], *, suite_name: str = "mcptest") -> str:
        """Return a complete self-contained HTML string for *results*."""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        errors = sum(
            1 for r in results
            if r.error is not None or not r.trace.succeeded
        )
        failed = total - passed - errors

        duration = sum(r.trace.duration_s for r in results)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Aggregate metric averages.
        metric_totals: dict[str, list[float]] = {}
        metric_labels: dict[str, str] = {}
        for r in results:
            for m in r.metrics:
                metric_totals.setdefault(m.name, []).append(m.score)
                metric_labels[m.name] = m.label

        metric_avgs: dict[str, float] = {
            name: sum(scores) / len(scores)
            for name, scores in metric_totals.items()
        }

        body_parts: list[str] = []
        body_parts.append(self._render_header(suite_name, ts, _VERSION))
        body_parts.append(self._render_summary(total, passed, failed, errors, duration))
        if metric_avgs:
            body_parts.append(self._render_metric_overview(metric_avgs, metric_labels))
        body_parts.append(self._render_table(results))
        body_parts.append(self._render_footer(ts, _VERSION))

        html = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>mcptest report — {_escape_html(suite_name)}</title>\n"
            f"<style>\n{_CSS}</style>\n"
            "</head>\n"
            "<body>\n"
            '<div class="container">\n'
            + "\n".join(body_parts)
            + "\n</div>\n"
            f"<script>{_JS}</script>\n"
            "</body>\n"
            "</html>"
        )
        return html

    # ------------------------------------------------------------------
    # Private render helpers
    # ------------------------------------------------------------------

    def _render_header(self, suite_name: str, ts: str, version: str) -> str:
        return (
            '<div class="header">\n'
            f'  <h1>mcptest — {_escape_html(suite_name)}</h1>\n'
            f'  <span class="badge badge-version">v{_escape_html(version)}</span>\n'
            "</div>\n"
            f'<div class="header-meta">{_escape_html(ts)}</div>'
        )

    def _render_summary(
        self,
        total: int,
        passed: int,
        failed: int,
        errors: int,
        duration: float,
    ) -> str:
        return (
            '<div class="summary">\n'
            f'  <div class="stat-card"><div class="num">{total}</div>'
            '<div class="lbl">Total</div></div>\n'
            f'  <div class="stat-card stat-pass"><div class="num">{passed}</div>'
            '<div class="lbl">Passed</div></div>\n'
            f'  <div class="stat-card stat-fail"><div class="num">{failed}</div>'
            '<div class="lbl">Failed</div></div>\n'
            f'  <div class="stat-card stat-warn"><div class="num">{errors}</div>'
            '<div class="lbl">Errors</div></div>\n'
            f'  <div class="stat-card"><div class="num">{duration:.1f}s</div>'
            '<div class="lbl">Duration</div></div>\n'
            "</div>"
        )

    def _render_metric_overview(
        self,
        metric_avgs: dict[str, float],
        metric_labels: dict[str, str],
    ) -> str:
        pills: list[str] = []
        for name, score in sorted(metric_avgs.items()):
            cls = _metric_color_class(score)
            label = metric_labels.get(name, "")
            pct = int(score * 100)
            pills.append(
                f'<div class="metric-pill {_escape_html(cls)}">\n'
                f'  <div class="metric-name">{_escape_html(name)}</div>\n'
                '  <div class="bar-track">'
                f'<div class="bar-fill" style="width:{pct}%"></div></div>\n'
                '  <div class="metric-score-row">'
                f'<span class="score-val">{score:.0%}</span>'
                f'<span class="score-lbl">{_escape_html(label)}</span>'
                "</div>\n"
                "</div>"
            )
        return (
            '<div class="section">\n'
            '<div class="section-title">Metric Overview</div>\n'
            '<div class="metric-grid">\n'
            + "\n".join(pills)
            + "\n</div>\n</div>"
        )

    def _render_table(self, results: list[Any]) -> str:
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        errors = sum(
            1 for r in results
            if r.error is not None or not r.trace.succeeded
        )
        failed = total - passed - errors

        rows: list[str] = []
        for idx, r in enumerate(results):
            rows.append(self._render_case_row(idx, r))

        return (
            '<div class="section">\n'
            '<div class="filters">\n'
            '  <button class="filter-btn active" data-filter="all">All</button>\n'
            f'  <button class="filter-btn" data-filter="pass">Passing ({passed})</button>\n'
            f'  <button class="filter-btn" data-filter="fail">Failing ({failed})</button>\n'
            f'  <button class="filter-btn" data-filter="error">Errors ({errors})</button>\n'
            f'  <span class="filter-count">{total} test{"s" if total != 1 else ""}</span>\n'
            "</div>\n"
            '<table class="results-table">\n'
            "<thead><tr>\n"
            '  <th data-sort="name">Test</th>\n'
            '  <th data-sort="status">Status</th>\n'
            '  <th data-sort="duration">Duration</th>\n'
            '  <th data-sort="tools">Tools</th>\n'
            '  <th data-sort="score">Score</th>\n'
            "</tr></thead>\n"
            '<tbody id="results-body">\n'
            + "\n".join(rows)
            + "\n</tbody>\n</table>\n</div>"
        )

    def _render_case_row(self, idx: int, r: Any) -> str:
        # Determine status
        if r.error is not None or not r.trace.succeeded:
            status = "error"
            pill = '<span class="pill pill-error">&#9888; ERROR</span>'
        elif r.passed:
            status = "pass"
            pill = '<span class="pill pill-pass">&#10003; PASS</span>'
        else:
            status = "fail"
            pill = '<span class="pill pill-fail">&#10007; FAIL</span>'

        duration = f"{r.trace.duration_s:.3f}s"
        tool_count = len(r.trace.tool_calls)
        avg_score = (
            sum(m.score for m in r.metrics) / len(r.metrics) if r.metrics else None
        )
        score_str = f"{avg_score:.2f}" if avg_score is not None else "—"
        score_val = f"{avg_score:.4f}" if avg_score is not None else "0"

        row_html = (
            f'<tr class="case-row" data-case="{idx}" data-status="{status}" '
            f'data-name="{_escape_html(r.case_name)}" '
            f'data-duration="{r.trace.duration_s:.6f}" '
            f'data-tools="{tool_count}" '
            f'data-score="{score_val}">\n'
            f'  <td><div class="case-name">{_escape_html(r.case_name)}'
            '<span class="expand-icon">&#9658;</span></div>\n'
            f'  <div class="suite-tag">{_escape_html(r.suite_name)}</div></td>\n'
            f"  <td>{pill}</td>\n"
            f"  <td>{_escape_html(duration)}</td>\n"
            f"  <td>{tool_count}</td>\n"
            f"  <td>{_escape_html(score_str)}</td>\n"
            "</tr>"
        )

        detail_html = (
            f'<tr class="detail-row" id="detail-{idx}">\n'
            f'  <td class="detail-cell" colspan="5">\n'
            f"    {self._render_detail(r)}\n"
            "  </td>\n"
            "</tr>"
        )

        return row_html + "\n" + detail_html

    def _render_detail(self, r: Any) -> str:
        panels: list[str] = []

        # -- Assertions panel --
        if r.assertion_results or r.error is not None or not r.trace.succeeded:
            items: list[str] = []
            if r.error is not None:
                items.append(
                    '<div class="assertion">'
                    '<span class="assertion-icon" style="color:var(--warn)">&#9888;</span>'
                    '<div><div class="assertion-name">Runner error</div>'
                    f'<div class="assertion-msg">{_escape_html(r.error)}</div></div>'
                    "</div>"
                )
            elif not r.trace.succeeded:
                agent_err = r.trace.agent_error or f"exit_code={r.trace.exit_code}"
                items.append(
                    '<div class="assertion">'
                    '<span class="assertion-icon" style="color:var(--warn)">&#9888;</span>'
                    '<div><div class="assertion-name">Agent error</div>'
                    f'<div class="assertion-msg">{_escape_html(agent_err)}</div></div>'
                    "</div>"
                )
            for a in r.assertion_results:
                icon_color = "var(--pass)" if a.passed else "var(--fail)"
                icon = "&#10003;" if a.passed else "&#10007;"
                details_html = ""
                if a.details:
                    details_text = "\n".join(
                        f"{_escape_html(k)}: {_escape_html(str(v))}"
                        for k, v in a.details.items()
                    )
                    details_html = (
                        f'<div class="assertion-details">{details_text}</div>'
                    )
                items.append(
                    '<div class="assertion">'
                    f'<span class="assertion-icon" style="color:{icon_color}">{icon}</span>'
                    "<div>"
                    f'<div class="assertion-name">{_escape_html(a.name)}</div>'
                    f'<div class="assertion-msg">{_escape_html(a.message)}</div>'
                    f"{details_html}"
                    "</div>"
                    "</div>"
                )
            panels.append(
                '<div class="detail-section">'
                "<h4>Assertions</h4>"
                + "\n".join(items)
                + "</div>"
            )

        # -- Tool calls panel --
        if r.trace.tool_calls:
            calls: list[str] = []
            for call in r.trace.tool_calls:
                err_cls = " tool-error" if call.is_error else ""
                args_json = _escape_html(
                    json.dumps(call.arguments, indent=2, default=str)
                )
                server_tag = (
                    f'<span class="tool-server">[{_escape_html(call.server_name)}]</span>'
                    if call.server_name
                    else ""
                )
                latency = f"{call.latency_ms:.1f}ms"
                result_html = ""
                if call.is_error:
                    result_html = (
                        f'<div class="tool-error-msg">'
                        f"Error: {_escape_html(str(call.error))}</div>"
                    )
                elif call.result is not None:
                    result_json = _escape_html(
                        json.dumps(call.result, indent=2, default=str)
                    )
                    result_html = f'<div class="tool-result">{result_json}</div>'

                calls.append(
                    f'<div class="tool-call{err_cls}">'
                    '<div class="tool-header">'
                    f'<span class="tool-name">{_escape_html(call.tool)}</span>'
                    f"{server_tag}"
                    f'<span class="tool-latency">{_escape_html(latency)}</span>'
                    "</div>"
                    f'<div class="tool-args">{args_json}</div>'
                    f"{result_html}"
                    "</div>"
                )
            panels.append(
                '<div class="detail-section">'
                "<h4>Tool Calls</h4>"
                + "\n".join(calls)
                + "</div>"
            )

        # -- Per-case metrics panel --
        if r.metrics:
            metric_rows: list[str] = []
            for m in r.metrics:
                cls = _metric_color_class(m.score)
                pct = int(m.score * 100)
                metric_rows.append(
                    f'<div class="case-metric-row {_escape_html(cls)}">'
                    f'<span class="case-metric-name">{_escape_html(m.name)}</span>'
                    '<span class="case-metric-bar">'
                    '<div class="bar-track">'
                    f'<div class="bar-fill" style="width:{pct}%"></div>'
                    "</div></span>"
                    f'<span class="case-metric-score score-val">{m.score:.2f}</span>'
                    "</div>"
                )
            panels.append(
                '<div class="detail-section">'
                "<h4>Metrics</h4>"
                + "\n".join(metric_rows)
                + "</div>"
            )

        if not panels:
            return '<div class="detail-inner"><div class="detail-section"><h4>No details</h4></div></div>'

        return (
            '<div class="detail-inner">'
            + "\n".join(panels)
            + "</div>"
        )

    def _render_footer(self, ts: str, version: str) -> str:
        return (
            '<div class="footer">'
            f"Generated by <strong>mcptest v{_escape_html(version)}</strong>"
            f" &middot; {_escape_html(ts)}"
            "</div>"
        )
