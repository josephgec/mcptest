"""Dashboard route handlers for the mcptest cloud web UI.

All routes live under the ``/dashboard`` prefix.  The router is built by the
``create_dashboard_router()`` factory so that ``Jinja2Templates`` is
initialised exactly once and closed over by every handler via a Python
closure—no module-level mutable state.

The ``get_db`` placeholder at module level lets the app factory override it
via ``app.dependency_overrides`` in exactly the same way as the API routers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mcptest.cloud.models import TestRun
from mcptest.cloud.webhooks.events import ALL_EVENTS
from mcptest.cloud.webhooks.models import Webhook, WebhookDelivery

_TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Placeholder dependency — overridden by the app factory
# ---------------------------------------------------------------------------


def get_db() -> Session:  # pragma: no cover
    """Placeholder — overridden via app.dependency_overrides at startup."""
    raise NotImplementedError("get_db must be overridden via app.dependency_overrides")


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------


def _time_ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{delta.days}d ago"


def _fmt_duration(duration_s: float | None) -> str:
    if duration_s is None:
        return "—"
    if duration_s < 1.0:
        return f"{duration_s * 1000:.0f}ms"
    return f"{duration_s:.2f}s"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_dashboard_router() -> APIRouter:
    """Build and return the /dashboard APIRouter.

    Called once by ``create_app()``; the returned router is registered via
    ``app.include_router()``.
    """
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    templates.env.filters["time_ago"] = _time_ago
    templates.env.filters["fmt_duration"] = _fmt_duration
    templates.env.filters["pct"] = _pct

    router = APIRouter(prefix="/dashboard", tags=["dashboard"])

    # ------------------------------------------------------------------
    # GET /dashboard/  — overview / home
    # ------------------------------------------------------------------

    @router.get("/")
    def dashboard_home(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ):
        total = db.scalar(select(func.count(TestRun.id))) or 0
        passed_count = (
            db.scalar(
                select(func.count(TestRun.id)).where(TestRun.passed.is_(True))
            )
            or 0
        )
        avg_duration = db.scalar(select(func.avg(TestRun.duration_s))) or 0.0
        total_tool_calls = (
            db.scalar(select(func.sum(TestRun.total_tool_calls))) or 0
        )
        baseline_count = (
            db.scalar(
                select(func.count(TestRun.id)).where(
                    TestRun.is_baseline.is_(True)
                )
            )
            or 0
        )
        pass_rate = round(passed_count / total * 100, 1) if total > 0 else 0.0

        recent_runs = list(
            db.scalars(
                select(TestRun).order_by(TestRun.created_at.desc()).limit(10)
            )
        )

        # Suite breakdown: {suite_name: {passed: int, failed: int}}
        suite_rows = db.execute(
            select(
                TestRun.suite,
                TestRun.passed,
                func.count(TestRun.id).label("n"),
            )
            .group_by(TestRun.suite, TestRun.passed)
            .order_by(TestRun.suite)
        ).all()
        suite_stats: dict[str, dict] = {}
        for suite, passed, count in suite_rows:
            key = suite or "(no suite)"
            if key not in suite_stats:
                suite_stats[key] = {"passed": 0, "failed": 0}
            if passed:
                suite_stats[key]["passed"] += count
            else:
                suite_stats[key]["failed"] += count

        return templates.TemplateResponse(
            request,
            "index.html",
            context={
                "active_page": "home",
                "total": total,
                "pass_rate": pass_rate,
                "avg_duration": avg_duration,
                "total_tool_calls": total_tool_calls,
                "baseline_count": baseline_count,
                "recent_runs": recent_runs,
                "suite_stats": suite_stats,
            },
        )

    # ------------------------------------------------------------------
    # GET /dashboard/runs  — filterable, paginated run list
    # ------------------------------------------------------------------

    @router.get("/runs")
    def dashboard_runs(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        suite: str | None = Query(None),
        branch: str | None = Query(None),
        passed: str | None = Query(None),  # "true" | "false" | None
        environment: str | None = Query(None),
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=100),
    ):
        stmt = select(TestRun).order_by(TestRun.created_at.desc())
        if suite:
            stmt = stmt.where(TestRun.suite == suite)
        if branch:
            stmt = stmt.where(TestRun.branch == branch)
        if passed == "true":
            stmt = stmt.where(TestRun.passed.is_(True))
        elif passed == "false":
            stmt = stmt.where(TestRun.passed.is_(False))
        if environment:
            stmt = stmt.where(TestRun.environment == environment)

        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        offset = (page - 1) * per_page
        runs = list(db.scalars(stmt.offset(offset).limit(per_page)))

        suites = sorted(
            r[0]
            for r in db.execute(select(TestRun.suite).distinct()).all()
            if r[0]
        )
        branches = sorted(
            r[0]
            for r in db.execute(select(TestRun.branch).distinct()).all()
            if r[0]
        )
        environments = sorted(
            r[0]
            for r in db.execute(select(TestRun.environment).distinct()).all()
            if r[0]
        )
        total_pages = max(1, (total + per_page - 1) // per_page)

        ctx = {
            "active_page": "runs",
            "runs": runs,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "suites": suites,
            "branches": branches,
            "environments": environments,
            "filter_suite": suite or "",
            "filter_branch": branch or "",
            "filter_passed": passed or "",
            "filter_environment": environment or "",
        }

        # Return partial HTML for htmx filter updates
        is_htmx = request.headers.get("HX-Request") == "true"
        template_name = "partials/runs_table.html" if is_htmx else "runs.html"
        return templates.TemplateResponse(request, template_name, context=ctx)

    # ------------------------------------------------------------------
    # GET /dashboard/runs/{run_id}  — single run detail
    # ------------------------------------------------------------------

    @router.get("/runs/{run_id}")
    def dashboard_run_detail(
        request: Request,
        run_id: int,
        db: Annotated[Session, Depends(get_db)],
    ):
        run = db.get(TestRun, run_id)
        if run is None:
            return templates.TemplateResponse(
                request,
                "404.html",
                context={"active_page": "runs", "run_id": run_id},
                status_code=404,
            )

        tool_calls = run.tool_calls or []
        if isinstance(tool_calls, str):
            tool_calls = json.loads(tool_calls)

        metric_scores = run.metric_scores or {}
        metric_items = sorted(metric_scores.items())

        return templates.TemplateResponse(
            request,
            "run_detail.html",
            context={
                "active_page": "runs",
                "run": run,
                "tool_calls": tool_calls,
                "metric_items": metric_items,
                "metric_scores_json": json.dumps(metric_scores),
            },
        )

    # ------------------------------------------------------------------
    # GET /dashboard/trends  — metric trend charts page
    # ------------------------------------------------------------------

    @router.get("/trends")
    def dashboard_trends(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ):
        suites = sorted(
            r[0]
            for r in db.execute(select(TestRun.suite).distinct()).all()
            if r[0]
        )
        branches = sorted(
            r[0]
            for r in db.execute(select(TestRun.branch).distinct()).all()
            if r[0]
        )
        # Collect all distinct metric names stored across runs
        metric_names: set[str] = set()
        for scores in db.scalars(select(TestRun.metric_scores)):
            if scores:
                metric_names.update(scores.keys())
        metrics = sorted(metric_names) or ["tool_efficiency"]

        return templates.TemplateResponse(
            request,
            "trends.html",
            context={
                "active_page": "trends",
                "suites": suites,
                "branches": branches,
                "metrics": metrics,
            },
        )

    # ------------------------------------------------------------------
    # GET /dashboard/trends/data  — JSON payload for Chart.js
    # ------------------------------------------------------------------

    @router.get("/trends/data")
    def dashboard_trends_data(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        suite: str | None = Query(None),
        branch: str | None = Query(None),
        metric: str = Query("tool_efficiency"),
        limit: int = Query(50, ge=10, le=200),
    ):
        stmt = select(TestRun).order_by(TestRun.created_at.asc())
        if suite:
            stmt = stmt.where(TestRun.suite == suite)
        if branch:
            stmt = stmt.where(TestRun.branch == branch)
        stmt = stmt.limit(limit)
        runs = list(db.scalars(stmt))

        labels: list[str] = []
        values: list[float | None] = []
        baseline_flags: list[bool] = []

        for run in runs:
            dt = run.created_at
            if dt is None:
                labels.append("")
            else:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                labels.append(dt.isoformat())
            scores = run.metric_scores or {}
            values.append(scores.get(metric))
            baseline_flags.append(bool(run.is_baseline))

        return JSONResponse(
            {
                "labels": labels,
                "values": values,
                "baseline_flags": baseline_flags,
                "metric": metric,
                "count": len(runs),
            }
        )

    # ------------------------------------------------------------------
    # GET /dashboard/baselines  — baseline management page
    # ------------------------------------------------------------------

    @router.get("/baselines")
    def dashboard_baselines(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ):
        baselines = list(
            db.scalars(
                select(TestRun)
                .where(TestRun.is_baseline.is_(True))
                .order_by(TestRun.created_at.desc())
            )
        )
        suites = sorted(
            r[0]
            for r in db.execute(select(TestRun.suite).distinct()).all()
            if r[0]
        )

        return templates.TemplateResponse(
            request,
            "baselines.html",
            context={
                "active_page": "baselines",
                "baselines": baselines,
                "suites": suites,
            },
        )

    # ------------------------------------------------------------------
    # GET /dashboard/webhooks  — webhook management page
    # ------------------------------------------------------------------

    @router.get("/webhooks")
    def dashboard_webhooks(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ):
        webhooks = list(
            db.scalars(select(Webhook).order_by(Webhook.created_at.desc()))
        )

        # Attach recent deliveries and success rate to each webhook
        webhook_data = []
        for wh in webhooks:
            recent = list(
                db.scalars(
                    select(WebhookDelivery)
                    .where(WebhookDelivery.webhook_id == wh.id)
                    .order_by(WebhookDelivery.created_at.desc())
                    .limit(5)
                )
            )
            total_deliveries = (
                db.scalar(
                    select(func.count(WebhookDelivery.id)).where(
                        WebhookDelivery.webhook_id == wh.id
                    )
                )
                or 0
            )
            success_count = (
                db.scalar(
                    select(func.count(WebhookDelivery.id)).where(
                        WebhookDelivery.webhook_id == wh.id,
                        WebhookDelivery.success.is_(True),
                    )
                )
                or 0
            )
            success_rate = (
                round(success_count / total_deliveries * 100)
                if total_deliveries > 0
                else None
            )
            webhook_data.append(
                {
                    "webhook": wh,
                    "recent_deliveries": recent,
                    "total_deliveries": total_deliveries,
                    "success_rate": success_rate,
                }
            )

        return templates.TemplateResponse(
            request,
            "webhooks.html",
            context={
                "active_page": "webhooks",
                "webhook_data": webhook_data,
                "all_events": ALL_EVENTS,
            },
        )

    return router
