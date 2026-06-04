#
# Copyright (C) 2020 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
MCP (Model Context Protocol) service for GNS3 server.

Implements the standard MCP protocol over SSE transport using FastMCP:

  /v3/mcp/sse     — SSE stream
  /v3/mcp/messages/ — JSON-RPC messages

Tools are registered via @mcp.tool() decorators.
"""

import contextvars
import json
import asyncio
import logging
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter
from fastapi.responses import Response

from mcp.server.fastmcp import FastMCP

from gns3server.config import Config

log = logging.getLogger(__name__)


# ── Per‑connection JWT token  ─────────────────────────────────────────
# Set during SSE authentication, read by tool handlers running in the
# same asyncio task (contextvars propagate through asyncio.to_thread).

_jwt_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_jwt_token", default=None
)


# ── Token validation ──────────────────────────────────────────────────

async def _validate_token(token: str) -> bool:
    """Return True if token is a valid GNS3 JWT."""
    from gns3server.services import auth_service
    try:
        auth_service.get_username_from_token(token)
        return True
    except Exception:
        return False


# ── Server URL helper ─────────────────────────────────────────────────

def _server_url() -> str:
    cfg = Config.instance().settings
    host = cfg.Server.host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    scheme = "https" if cfg.Server.enable_ssl else "http"
    return f"{scheme}://{host}:{cfg.Server.port}"


# ── FastMCP Server ────────────────────────────────────────────────────

mcp = FastMCP("GNS3 MCP Server")


# ── Tool handlers ─────────────────────────────────────────────────────

def _run_handler_sync(handler, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a synchronous Gns3Connector handler in a thread."""
    ctx = {
        "server_url": _server_url(),
        "jwt_token": _jwt_token_var.get(),
    }
    result = handler(params, ctx)
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]


@mcp.tool()
async def list_projects() -> list[dict[str, Any]]:
    """List all GNS3 projects accessible to the current user."""
    from .projects import list_projects_handler
    return await asyncio.to_thread(_run_handler_sync, list_projects_handler, {})


@mcp.tool()
async def get_project(project_id: str) -> list[dict[str, Any]]:
    """Get detailed information about a specific project.

    Args:
        project_id: Project UUID
    """
    from .projects import get_project_handler
    return await asyncio.to_thread(_run_handler_sync, get_project_handler, {"project_id": project_id})


@mcp.tool()
async def create_project(name: str, description: str = "") -> list[dict[str, Any]]:
    """Create a new GNS3 project.

    Args:
        name: Project name
        description: Optional project description
    """
    from .projects import create_project_handler
    params = {"name": name}
    if description:
        params["description"] = description
    return await asyncio.to_thread(_run_handler_sync, create_project_handler, params)


@mcp.tool()
async def delete_project(project_id: str) -> list[dict[str, Any]]:
    """Delete a GNS3 project permanently.

    Args:
        project_id: UUID of the project to delete
    """
    from .projects import delete_project_handler
    return await asyncio.to_thread(_run_handler_sync, delete_project_handler, {"project_id": project_id})


@mcp.tool()
async def open_project(project_id: str) -> list[dict[str, Any]]:
    """Open a closed GNS3 project.

    Args:
        project_id: Project UUID
    """
    from .projects import open_project_handler
    return await asyncio.to_thread(_run_handler_sync, open_project_handler, {"project_id": project_id})


@mcp.tool()
async def close_project(project_id: str) -> list[dict[str, Any]]:
    """Close an open GNS3 project.

    Args:
        project_id: Project UUID
    """
    from .projects import close_project_handler
    return await asyncio.to_thread(_run_handler_sync, close_project_handler, {"project_id": project_id})


@mcp.tool()
async def get_project_stats(project_id: str) -> list[dict[str, Any]]:
    """Get statistics (nodes, links, snapshots, drawings) for a project.

    Args:
        project_id: Project UUID
    """
    from .projects import get_project_stats_handler
    return await asyncio.to_thread(_run_handler_sync, get_project_stats_handler, {"project_id": project_id})


# ── Auth‑wrapped SSE app ──────────────────────────────────────────────

def _make_auth_wrapper(inner_app):
    """Wrap the SSE app with JWT validation.

    Supports two ways to pass the token (checked in order):
      1. Authorization: Bearer <jwt> header
      2. ?token=<jwt> query parameter

    POST messages are passed through (authenticated by their session).
    """

    async def auth_wrapper(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "GET":
            token = None
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            if auth.startswith("Bearer "):
                token = auth[7:]
            if not token:
                params = parse_qs(scope.get("query_string", b"").decode())
                tokens = params.get("token", [])
                if tokens:
                    token = tokens[0]
            if not token or not await _validate_token(token):
                response = Response("Missing or invalid token", status_code=401)
                await response(scope, receive, send)
                return
            _jwt_token_var.set(token)
        await inner_app(scope, receive, send)

    return auth_wrapper


# ── FastAPI router ────────────────────────────────────────────────────

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/")
async def mcp_root():
    """MCP service metadata."""
    return {
        "name": "GNS3 MCP Server",
        "version": "1.0.0",
        "authentication": ["Authorization: Bearer <jwt>", "?token=<jwt>"],
        "transports": {
            "sse": "/v3/mcp/transport/sse",
        },
    }


def register_starlette_routes(app):
    """Mount MCP transports on the FastAPI app."""
    sse_app = _make_auth_wrapper(mcp.sse_app(mount_path=""))
    app.mount("/v3/mcp/transport", sse_app, name="mcp-sse")
    log.info("MCP SSE server mounted at /v3/mcp/transport")
