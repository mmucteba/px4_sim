"""DATABOSS dashboard - FastAPI app factory.

    uvicorn.run("databoss_sim.dashboard.app:create_app", factory=True, ...)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from databoss_sim.dashboard.config import EXPERIMENTS_ROOT
from databoss_sim.dashboard.routers import checks, comparisons, runs, scenarios, worlds
from databoss_sim.dashboard.terrain_generator_proxy import router as terrain_generator_proxy_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    checks.refresh_model_sync_cache()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="DATABOSS Dashboard", lifespan=_lifespan)
    app.include_router(runs.router)
    app.include_router(comparisons.router)
    app.include_router(checks.router)
    app.include_router(scenarios.router)
    app.include_router(worlds.router)
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

    @app.get("/create", include_in_schema=False)
    def serve_create_page() -> FileResponse:
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

    return app
