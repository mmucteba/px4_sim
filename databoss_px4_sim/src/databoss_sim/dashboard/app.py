"""DATABOSS dashboard - FastAPI app factory.

    uvicorn.run("databoss_sim.dashboard.app:create_app", factory=True, ...)
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from databoss_sim.dashboard.config import EXPERIMENTS_ROOT, JOB_REAPER_INTERVAL_S
from databoss_sim.dashboard.job_registry import TERMINAL_STATUSES, list_jobs, reconcile
from databoss_sim.dashboard.routers import checks, comparisons, jobs, runs, scenarios, worlds
from databoss_sim.dashboard.terrain_generator_proxy import router as terrain_generator_proxy_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


class DashboardFastAPI(FastAPI):
    @property
    def routes(self) -> list:
        flattened = []
        for route in self.router.routes:
            route_contexts = getattr(route, "effective_route_contexts", None)
            if callable(route_contexts):
                for context in route_contexts():
                    flattened.append(context.starlette_route or context.original_route)
            else:
                flattened.append(route)
        return flattened


def _reconcile_nonterminal_jobs() -> None:
    for record in list_jobs(limit=200):
        if record.status not in TERMINAL_STATUSES:
            reconcile(record)


async def _job_reaper_loop() -> None:
    while True:
        await asyncio.to_thread(_reconcile_nonterminal_jobs)
        await asyncio.sleep(JOB_REAPER_INTERVAL_S)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    checks.refresh_model_sync_cache()
    reaper_task = asyncio.create_task(_job_reaper_loop())
    try:
        yield
    finally:
        reaper_task.cancel()
        with suppress(asyncio.CancelledError):
            await reaper_task


def create_app() -> FastAPI:
    app = DashboardFastAPI(title="DATABOSS Dashboard", lifespan=_lifespan)
    app.include_router(runs.router)
    app.include_router(comparisons.router)
    app.include_router(checks.router)
    app.include_router(scenarios.router)
    app.include_router(worlds.router)
    app.include_router(jobs.router)
    app.include_router(terrain_generator_proxy_router)

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/runs/{run_id}", include_in_schema=False)
    def serve_run_page(run_id: str) -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/comparisons/{comparison_id}", include_in_schema=False)
    def serve_comparison_page(comparison_id: str) -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/comparisons", include_in_schema=False)
    def serve_comparisons_page() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/scenarios", include_in_schema=False)
    def serve_scenarios_page() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/scenarios/{name}", include_in_schema=False)
    def serve_scenario_page(name: str) -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/health", include_in_schema=False)
    def serve_health_page() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/create", include_in_schema=False)
    def serve_create_page() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/jobs", include_in_schema=False)
    def serve_jobs_page() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/jobs/{job_id}", include_in_schema=False)
    def serve_job_page(job_id: str) -> FileResponse:
        return FileResponse(INDEX_HTML)

    # Direct filesystem serving of existing run/comparison artifacts
    # (plots, configs, logs) - zero copying, matches EXPERIMENTS_ROOT layout
    # exactly: /artifacts/runs/<id>/..., /artifacts/comparisons/<id>/...
    # follow_symlink=True: experiments/runs etc. may be a symlink (e.g. this
    # dev worktree links back to the real data directory to avoid
    # duplicating gigabytes of run output).
    app.mount(
        "/artifacts",
        StaticFiles(directory=EXPERIMENTS_ROOT, follow_symlink=True),
        name="artifacts",
    )

    # CSS/JS for the dashboard's own frontend (ES modules, no bundler).
    # Referenced with absolute paths (/static/...) because index.html is
    # served from several different route paths (/, /runs/{id}, /create,
    # ...) - a relative asset path would resolve differently depending on
    # which route served the page (the exact bug already found and fixed
    # once this session for the terrain-generator proxy).
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
