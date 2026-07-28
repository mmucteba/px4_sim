"""Reverse proxy to the gazebo_terrain_generator web UI (127.0.0.1:8080,
loopback-only - an external tool this dashboard doesn't embed, just makes
reachable). User request (2026-07-24): "connect port 8080 into this app,
let user create their terrain and then we convert them to useful worlds."

The proxy target's own JS (main.js, inspected directly, not guessed) mixes
two path styles that need different handling:
- Static page/assets (`/`, `css/main.css`, `js/main.js`) use RELATIVE
  paths, so serving them under a subpath prefix (`/terrain-generator/...`)
  works correctly - the browser resolves them relative to the page's own
  loaded URL.
- The app's own fetch() calls hardcode ABSOLUTE root paths
  (`/start-download`, `/task-status`, etc. - exactly 7, enumerated in
  terrain_generator_proxy.PROXIED_API_PATHS below, confirmed by grepping
  every fetch()/`.href =` in main.js, not assumed). Those must be proxied
  at the DASHBOARD's own root to match what the browser actually requests.

A handful of the app's own fetch() calls go straight to
`https://api.mapbox.com/...` (Mapbox's real API, for map tiles/geocoding/
key validation) - those need no proxying at all; the browser reaches them
directly over normal internet access, unrelated to this proxy or to
Tailscale reachability.

The running generator instance already writes its output directly into
generated_worlds/terrain/_generator_output/ (confirmed via its own process
environment: GAZEBO_TERRAIN_OUTPUT_PATH is set to exactly that path) - the
same directory world_generation.list_unimported_terrain_packages() already
scans. Nothing extra is needed to connect generation output to the import
step; they already share the same directory.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

UPSTREAM_BASE = "http://127.0.0.1:8080"

# Confirmed exhaustively via `grep -nE "fetch\(|\.href\s*=" main.js` against
# the actual running app, 2026-07-24 - not a guess. Each maps to the HTTP
# method(s) main.js actually uses for it.
PROXIED_API_PATHS: dict[str, list[str]] = {
    "/start-download": ["POST"],
    "/download-tile": ["POST"],
    "/end-download": ["POST"],
    "/task-status": ["GET"],
    "/valid-heightmap-sizes": ["GET"],
    "/estimate-texture-sizes": ["POST"],
    "/polygon-info": ["POST"],
    "/download-world": ["GET"],
}

router = APIRouter()

# Headers that are connection/host-specific and must not be forwarded
# verbatim in either direction (httpx sets its own, and passing the
# original Host/Content-Length through can produce a broken request).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


async def _proxy(request: Request, upstream_path: str) -> Response:
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}

    async with httpx.AsyncClient(timeout=120.0) as client:
        upstream_response = await client.request(
            request.method,
            f"{UPSTREAM_BASE}{upstream_path}",
            params=request.query_params,
            headers=headers,
            content=body,
        )

    response_headers = {
        k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


@router.get("/terrain-generator", include_in_schema=False)
async def redirect_terrain_generator_ui() -> RedirectResponse:
    # Without the trailing slash, the browser resolves the proxied page's
    # own relative asset paths (href="css/main.css", confirmed in its
    # actual HTML - no leading slash) against /terrain-generator's PARENT,
    # producing /css/main.css instead of /terrain-generator/css/main.css.
    # Standard fix for this exact "directory-style" URL problem: redirect
    # to the trailing-slash form before ever serving the page.
    return RedirectResponse(url="/terrain-generator/")


# GET only, deliberately. This catch-all exists to serve the upstream app's
# static page and relative assets; per the module docstring above, every POST
# main.js makes goes to an absolute root path in PROXIED_API_PATHS instead, so
# allowing POST here buys nothing. It did cost something: it turned the
# dashboard into an unauthenticated arbitrary-POST relay into a loopback-only
# service that was unreachable from the tailnet before this proxy existed.
@router.api_route("/terrain-generator/{path:path}", methods=["GET"])
async def proxy_terrain_generator_ui(request: Request, path: str = "") -> Response:
    upstream_path = f"/{path}" if path else "/"
    return await _proxy(request, upstream_path)


def _make_api_proxy_handler(api_path: str):
    async def handler(request: Request) -> Response:
        return await _proxy(request, api_path)

    handler.__name__ = f"proxy_{api_path.strip('/').replace('-', '_')}"
    return handler


for _api_path, _methods in PROXIED_API_PATHS.items():
    router.add_api_route(_api_path, _make_api_proxy_handler(_api_path), methods=_methods)
