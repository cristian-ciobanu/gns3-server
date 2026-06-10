#
# Copyright (C) 2026 GNS3 Technologies Inc.
# Author: Yue Guobin
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
import socket
from typing import Any, Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter
from fastapi.responses import Response

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from gns3server.config import Config
from gns3server.services import auth_service
from gns3server.utils.request_utils import extract_client_info
from .projects import (
    list_projects_handler, get_project_handler, create_project_handler,
    delete_project_handler, open_project_handler, close_project_handler,
    get_project_stats_handler, update_project_handler, duplicate_project_handler,
    get_project_readme_handler, update_project_readme_handler,
    lock_project_handler, unlock_project_handler,
    load_project_handler, get_locked_project_handler,
)
from .server import (
    get_version_handler, get_statistics_handler,
)
from .symbols import (
    get_symbols_handler, get_symbol_handler,
    get_symbol_dimensions_handler, get_default_symbols_handler,
    upload_symbol_handler, delete_symbol_handler,
)
from .appliances import (
    get_appliances_handler, get_appliance_handler,
    install_appliance_handler,
)
from .images import (
    get_images_handler, get_image_handler,
    delete_image_handler, prune_images_handler,
    install_images_handler,
)
from .device_config import (
    device_config_send_handler, device_command_run_handler,
    vpcs_config_set_handler,
)
from .nodes import (
    get_nodes_handler, get_node_handler, start_node_handler,
    stop_node_handler, reload_node_handler, suspend_node_handler,
    create_node_handler, delete_node_handler, update_node_handler,
    get_node_console_info_handler,
    list_node_files_handler, get_node_file_handler,
    write_node_file_handler, delete_node_file_handler,
    start_all_nodes_handler, stop_all_nodes_handler,
    suspend_all_nodes_handler, reload_all_nodes_handler,
    duplicate_node_handler, isolate_node_handler,
    unisolate_node_handler, get_node_links_handler,
)
from .links import (
    get_links_handler, get_link_handler, create_link_handler,
    delete_link_handler, update_link_handler,
    reset_link_handler, start_capture_handler, stop_capture_handler,
    download_capture_file_handler,
)
from .templates import (
    list_templates_handler, get_template_handler, create_template_handler,
    update_template_handler, delete_template_handler,
)
from .computes import (
    list_computes_handler, get_compute_handler, get_compute_images_handler,
)
from .snapshots import (
    get_snapshots_handler, create_snapshot_handler,
    delete_snapshot_handler, restore_snapshot_handler,
)
from .drawings import (
    get_drawings_handler, create_drawing_handler,
    get_drawing_handler, update_drawing_handler, delete_drawing_handler,
)

log = logging.getLogger(__name__)


# ── Server ready state ────────────────────────────────────────────────
# Tracks whether GNS3 server has completed initialization.
# MCP connections wait up to 5 seconds for startup to complete, then return
# 503 Service Unavailable if initialization is not complete to prevent
# "Received request before initialization was complete" errors.

_mcp_ready_event = asyncio.Event()


def set_mcp_server_ready(ready: bool = True) -> None:
    """
    Set MCP server ready state.

    Should be called after GNS3 startup completes (database, controller, etc.)
    to allow MCP connections to proceed.

    Args:
        ready: True to mark server as ready, False to mark as not ready
    """
    if ready:
        _mcp_ready_event.set()
        log.info("MCP server is now ready to accept connections")
    else:
        _mcp_ready_event.clear()


async def wait_for_mcp_ready() -> bool:
    """
    Wait until MCP server is ready before accepting connections.

    Returns:
        True if server is ready, False if timeout reached

    Returns immediately if already ready. Otherwise waits with a timeout
    and returns False if server does not become ready in time.
    """
    if _mcp_ready_event.is_set():
        return True

    log.debug("MCP server not ready yet, waiting for initialization to complete...")

    try:
        await asyncio.wait_for(_mcp_ready_event.wait(), timeout=5.0)
        log.debug("MCP server is now ready, proceeding with connection")
        return True
    except asyncio.TimeoutError:
        log.warning(
            "MCP server ready check timed out after 5 seconds - "
            "GNS3 server initialization may have issues"
        )
        return False


# ── Per‑connection JWT token  ─────────────────────────────────────────
# Set during SSE authentication, read by tool handlers running in the
# same asyncio task (contextvars propagate through asyncio.to_thread).

_jwt_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_jwt_token", default=None
)


# ── Token validation ──────────────────────────────────────────────────

async def _validate_token(token: str) -> bool:
    """Return True if token is a valid GNS3 JWT."""
    try:
        auth_service.get_username_from_token(token)
        return True
    except Exception:
        return False


# ── Server URL helper ─────────────────────────────────────────────────

def _server_url() -> str:
    cfg = Config.instance().settings
    host = cfg.Server.host
    if host in ("0.0.0.0", "::"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.1)
                s.connect(("8.8.8.8", 80))
                host = s.getsockname()[0]
        except OSError:
            host = "127.0.0.1"
    scheme = "https" if cfg.Server.enable_ssl else "http"
    return f"{scheme}://{host}:{cfg.Server.port}"


# ── FastMCP Server ────────────────────────────────────────────────────

def _create_mcp_server() -> FastMCP:
    """Create MCP server with security settings from configuration."""
    cfg = Config.instance().settings.Server

    # Always pass an explicit TransportSecuritySettings to prevent FastMCP
    # from auto-enabling protection when host is localhost (its default).
    if cfg.mcp_enable_dns_rebinding_protection:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=cfg.mcp_allowed_hosts or ["127.0.0.1:*", "localhost:*"],
            allowed_origins=cfg.mcp_allowed_origins or ["http://127.0.0.1:*", "http://localhost:*"],
        )
    else:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )

    mcp = FastMCP("GNS3 MCP Server", transport_security=transport_security)
    return mcp


mcp = _create_mcp_server()


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
async def project_list() -> list[dict[str, Any]]:
    """List all GNS3 projects accessible to the current user."""
    return await asyncio.to_thread(_run_handler_sync, list_projects_handler, {})


@mcp.tool()
async def project_get(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Get detailed information about a specific project."""
    return await asyncio.to_thread(_run_handler_sync, get_project_handler, {"project_id": project_id})


@mcp.tool()
async def project_create(
    name: Annotated[str, Field(description="Project name")],
    description: Annotated[str, Field(description="Optional project description")] = "",
) -> list[dict[str, Any]]:
    """Create a new GNS3 project."""
    params = {"name": name}
    if description:
        params["description"] = description
    return await asyncio.to_thread(_run_handler_sync, create_project_handler, params)


@mcp.tool()
async def project_delete(
    project_id: Annotated[str, Field(description="UUID of the project to delete")],
) -> list[dict[str, Any]]:
    """Delete a GNS3 project permanently."""
    return await asyncio.to_thread(_run_handler_sync, delete_project_handler, {"project_id": project_id})


@mcp.tool()
async def project_open(
    project_id: Annotated[str, Field(description="UUID of the project to open")],
) -> list[dict[str, Any]]:
    """Open a closed GNS3 project."""
    return await asyncio.to_thread(_run_handler_sync, open_project_handler, {"project_id": project_id})

@mcp.tool()
async def project_close(
    project_id: Annotated[str, Field(description="UUID of the project to close")],
) -> list[dict[str, Any]]:
    """Close an open GNS3 project."""
    return await asyncio.to_thread(_run_handler_sync, close_project_handler, {"project_id": project_id})

@mcp.tool()
async def project_stats(
    project_id: Annotated[str, Field(description="UUID of the project to get statistics for")],
) -> list[dict[str, Any]]:
    """Get statistics (nodes, links, snapshots, drawings) for a project."""
    return await asyncio.to_thread(_run_handler_sync, get_project_stats_handler, {"project_id": project_id})


@mcp.tool()
async def project_update(
    project_id: Annotated[str, Field(description="UUID of the project to update")],
    name: Annotated[str, Field(description="New project name")] = None,
    auto_close: Annotated[bool, Field(description="Close project when last client leaves")] = None,
    auto_open: Annotated[bool, Field(description="Project opens when GNS3 starts")] = None,
    auto_start: Annotated[bool, Field(description="Project starts when opened")] = None,
    scene_width: Annotated[int, Field(description="Width of the drawing area")] = None,
    scene_height: Annotated[int, Field(description="Height of the drawing area")] = None,
    zoom: Annotated[int, Field(description="Zoom of the drawing area")] = None,
    show_layers: Annotated[bool, Field(description="Show layers on the drawing area")] = None,
    snap_to_grid: Annotated[bool, Field(description="Snap to grid on the drawing area")] = None,
    show_grid: Annotated[bool, Field(description="Show the grid on the drawing area")] = None,
    grid_size: Annotated[int, Field(description="Grid size for the drawing area for nodes")] = None,
    drawing_grid_size: Annotated[int, Field(description="Grid size for the drawing area for drawings")] = None,
    show_interface_labels: Annotated[bool, Field(description="Show interface labels on the drawing area")] = None,
) -> list[dict[str, Any]]:
    """Update a project's properties (name, auto_close, auto_open, etc.)."""
    params = {"project_id": project_id}
    local_vars = {
        "name": name, "auto_close": auto_close, "auto_open": auto_open, "auto_start": auto_start,
        "scene_width": scene_width, "scene_height": scene_height, "zoom": zoom,
        "show_layers": show_layers, "snap_to_grid": snap_to_grid, "show_grid": show_grid,
        "grid_size": grid_size, "drawing_grid_size": drawing_grid_size, "show_interface_labels": show_interface_labels,
    }
    for key, val in local_vars.items():
        if val is not None:
            params[key] = val
    return await asyncio.to_thread(_run_handler_sync, update_project_handler, params)


@mcp.tool()
async def project_duplicate(
    project_id: Annotated[str, Field(description="UUID of the project to duplicate")],
    name: Annotated[str, Field(description="New project name")],
    reset_mac_addresses: Annotated[bool, Field(description="Reset MAC addresses for this project")] = False,
) -> list[dict[str, Any]]:
    """Duplicate a project."""
    params = {"project_id": project_id, "name": name}
    if reset_mac_addresses:
        params["reset_mac_addresses"] = reset_mac_addresses
    return await asyncio.to_thread(_run_handler_sync, duplicate_project_handler, params)


@mcp.tool()
async def project_readme_get(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Get the content of a project's README.md file — the project documentation (Markdown format)."""
    return await asyncio.to_thread(_run_handler_sync, get_project_readme_handler, {"project_id": project_id})


@mcp.tool()
async def project_readme_update(
    project_id: Annotated[str, Field(description="UUID of the project")],
    content: Annotated[str, Field(description="Content to write to README.md (Markdown format)")],
) -> list[dict[str, Any]]:
    """Update or create a project's README.md file — the project documentation (Markdown format)."""
    return await asyncio.to_thread(_run_handler_sync, update_project_readme_handler, {"project_id": project_id, "content": content})


# ── Node tools ────────────────────────────────────────────────────────

@mcp.tool()
async def node_list(project_id: str) -> list[dict[str, Any]]:
    """List all nodes in a project."""
    return await asyncio.to_thread(_run_handler_sync, get_nodes_handler, {"project_id": project_id})


@mcp.tool()
async def node_get(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node")],
) -> list[dict[str, Any]]:
    """Get detailed information about a specific node."""
    return await asyncio.to_thread(_run_handler_sync, get_node_handler, {"project_id": project_id, "node_id": node_id})

@mcp.tool()
async def node_start(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to start")],
) -> list[dict[str, Any]]:
    """Start a node in a project."""
    return await asyncio.to_thread(_run_handler_sync, start_node_handler, {"project_id": project_id, "node_id": node_id})

@mcp.tool()
async def node_stop(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to stop")],
) -> list[dict[str, Any]]:
    """Stop a node in a project."""
    return await asyncio.to_thread(_run_handler_sync, stop_node_handler, {"project_id": project_id, "node_id": node_id})

@mcp.tool()
async def node_reload(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to reload")],
) -> list[dict[str, Any]]:
    """Reload (restart) a node in a project."""
    return await asyncio.to_thread(_run_handler_sync, reload_node_handler, {"project_id": project_id, "node_id": node_id})

@mcp.tool()
async def node_suspend(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to suspend")],
) -> list[dict[str, Any]]:
    """Suspend a node in a project."""
    return await asyncio.to_thread(_run_handler_sync, suspend_node_handler, {"project_id": project_id, "node_id": node_id})


@mcp.tool()
async def node_create(
    project_id: Annotated[str, Field(description="UUID of the project")],
    template_id: Annotated[str, Field(description="UUID of the template to create the node from")],
    x: Annotated[int, Field(description="X coordinate on the project canvas")] = 0,
    y: Annotated[int, Field(description="Y coordinate on the project canvas")] = 0,
    compute_id: Annotated[str, Field(description="Compute ID (default: local)")] = "local",
) -> list[dict[str, Any]]:
    """Create a new node from a template in a project."""
    return await asyncio.to_thread(_run_handler_sync, create_node_handler, {
        "project_id": project_id, "template_id": template_id,
        "x": x, "y": y, "compute_id": compute_id,
    })


@mcp.tool()
async def node_delete(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to delete")],
) -> list[dict[str, Any]]:
    """Delete a node from a project."""
    return await asyncio.to_thread(_run_handler_sync, delete_node_handler, {"project_id": project_id, "node_id": node_id})


@mcp.tool()
async def node_update(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to update")],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Update a node's properties (name, position, etc.)."""
    params = {"project_id": project_id, "node_id": node_id, **kwargs}
    return await asyncio.to_thread(_run_handler_sync, update_node_handler, params)


@mcp.tool()
async def node_console(
    project_id: Annotated[str, Field(description="UUID of the project containing the node")],
    node_id: Annotated[str, Field(description="UUID of the node to get console info for")],
) -> list[dict[str, Any]]:
    """Get WebSocket console connection info for a node.

    Returns the WebSocket URL, console type (telnet/ssh/vnc), and other
    connection details needed to interact with a node's console via WebSocket.

    Complete workflow:
      1. Call this tool with project_id and node_id to get the WebSocket URL
      2. Connect to the returned URL using websocat in text mode (-t):
         > websocat -t "ws://<your-gns3-server-host>:3080/v3/projects/{project_id}/nodes/{node_id}/console/ws?token={jwt_token}"
      3. Send device commands with \\r\\n line endings via heredoc:
         > websocat -t "ws://..." <<< $'\\r\\nenable\\r\\nshow version\\r\\nexit\\r\\n'
      4. Receive response: websocat receives and displays device output
         Use 'timeout' to avoid connection hanging:
         > timeout 10 websocat -t "ws://..." <<< $'commands\\r\\n'

    Key points:
      - Use \\r\\n (not \\n) to match console protocol line endings
      - Use $'...' format for escape sequences in bash
      - Set a timeout to prevent hanging connections
    """
    return await asyncio.to_thread(_run_handler_sync, get_node_console_info_handler, {
        "project_id": project_id, "node_id": node_id,
    })


# ── Link tools ────────────────────────────────────────────────────────

@mcp.tool()
async def link_list(project_id: str) -> list[dict[str, Any]]:
    """List all links in a project."""
    return await asyncio.to_thread(_run_handler_sync, get_links_handler, {"project_id": project_id})


@mcp.tool()
async def link_get(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link")],
) -> list[dict[str, Any]]:
    """Get detailed information about a specific link."""
    return await asyncio.to_thread(_run_handler_sync, get_link_handler, {"project_id": project_id, "link_id": link_id})


@mcp.tool()
async def link_create(
    project_id: Annotated[str, Field(description="UUID of the project")],
    nodes: Annotated[list, Field(description="List of node connections, e.g. [{\"node_id\": \"...\", \"adapter_number\": 0, \"port_number\": 0}]")],
    link_type: Annotated[str, Field(description="Link type - ethernet or serial")] = "ethernet",
    filters: Annotated[dict, Field(description="Optional packet filters (must use array format): frequency_drop: [N], packet_loss: [rate], delay: [ms, jitter], corrupt: [rate], bpf: [expression]")] = None,
) -> list[dict[str, Any]]:
    """Create a link between two nodes in a project.

    Filters must use array format:
    - frequency_drop: [N] - Drop every Nth packet (N: -1 to 32767)
    - packet_loss: [rate] - Packet loss percentage (rate: 0 to 100)
    - delay: [ms, jitter] - Latency and jitter in milliseconds
    - corrupt: [rate] - Packet corruption percentage (rate: 0 to 100)
    - bpf: [expression] - Berkeley Packet Filter expression

    Example: {"filters": {"delay": [100, 10], "packet_loss": [5]}}
    """
    params = {"project_id": project_id, "nodes": nodes, "link_type": link_type}
    if filters:
        params["filters"] = filters
    return await asyncio.to_thread(_run_handler_sync, create_link_handler, params)


@mcp.tool()
async def link_delete(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link to delete")],
) -> list[dict[str, Any]]:
    """Delete a link from a project."""
    return await asyncio.to_thread(_run_handler_sync, delete_link_handler, {"project_id": project_id, "link_id": link_id})


@mcp.tool()
async def link_update(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link to update")],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Update a link's properties (suspend, filters, etc.).

    Supported kwargs:
    - suspend: boolean - Suspend or resume the link
    - filters: dict - Packet filters (must use array format):
      * frequency_drop: [N] - Drop every Nth packet (N: -1 to 32767)
      * packet_loss: [rate] - Packet loss percentage (rate: 0 to 100)
      * delay: [ms, jitter] - Latency and jitter in milliseconds
      * corrupt: [rate] - Packet corruption percentage (rate: 0 to 100)
      * bpf: [expression] - Berkeley Packet Filter expression

    Example filters:
      {"filters": {"frequency_drop": [10]}}
      {"filters": {"delay": [100, 10]}}
      {"filters": {"packet_loss": [5]}}
      {"filters": {"delay": [50, 5], "packet_loss": [2]}}
    """
    params = {"project_id": project_id, "link_id": link_id, **kwargs}
    return await asyncio.to_thread(_run_handler_sync, update_link_handler, params)


# ── Template tools ────────────────────────────────────────────────────

@mcp.tool()
async def template_list() -> list[dict[str, Any]]:
    """List all available templates on the server."""
    return await asyncio.to_thread(_run_handler_sync, list_templates_handler, {})


@mcp.tool()
async def template_get(
    template_id: Annotated[str | None, Field(description="Template UUID (optional if name is provided)")] = None,
    name: Annotated[str | None, Field(description="Template name (optional if template_id is provided)")] = None,
) -> list[dict[str, Any]]:
    """Get detailed information about a specific template."""
    return await asyncio.to_thread(_run_handler_sync, get_template_handler, {
        "template_id": template_id, "name": name,
    })


@mcp.tool()
async def template_create(
    name: Annotated[str, Field(description="Template name")],
    template_type: Annotated[str, Field(description="Template type (e.g. qemu, docker, dynamips)")],
    compute_id: Annotated[str, Field(description="Compute ID (default: local)")] = "local",
) -> list[dict[str, Any]]:
    """Create a new template."""
    return await asyncio.to_thread(_run_handler_sync, create_template_handler, {
        "name": name, "template_type": template_type, "compute_id": compute_id,
    })


@mcp.tool()
async def template_update(
    template_id: Annotated[str | None, Field(description="Template UUID (optional if name is provided)")] = None,
    name: Annotated[str | None, Field(description="Template name (optional if template_id is provided)")] = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Update an existing template's properties."""
    params = {"template_id": template_id, "name": name, **kwargs}
    return await asyncio.to_thread(_run_handler_sync, update_template_handler, params)


@mcp.tool()
async def template_delete(
    template_id: Annotated[str | None, Field(description="Template UUID (optional if name is provided)")] = None,
    name: Annotated[str | None, Field(description="Template name (optional if template_id is provided)")] = None,
) -> list[dict[str, Any]]:
    """Delete a template."""
    return await asyncio.to_thread(_run_handler_sync, delete_template_handler, {
        "template_id": template_id, "name": name,
    })


# ── Compute tools ─────────────────────────────────────────────────────

@mcp.tool()
async def compute_list() -> list[dict[str, Any]]:
    """List all compute nodes available to the server."""
    return await asyncio.to_thread(_run_handler_sync, list_computes_handler, {})


@mcp.tool()
async def compute_get(
    compute_id: Annotated[str, Field(description="Compute UUID from compute_list output")],
) -> list[dict[str, Any]]:
    """Get detailed information about a compute node. Use compute_list first to get the UUID."""
    return await asyncio.to_thread(_run_handler_sync, get_compute_handler, {"compute_id": compute_id})


@mcp.tool()
async def compute_images(
    emulator: Annotated[str, Field(description="Emulator type (e.g. qemu, iou, docker)")],
    compute_id: Annotated[str, Field(description="Compute UUID from compute_list output")],
) -> list[dict[str, Any]]:
    """List available images for an emulator on a compute node."""
    return await asyncio.to_thread(_run_handler_sync, get_compute_images_handler, {
        "emulator": emulator, "compute_id": compute_id,
    })


# ── Node file tools ────────────────────────────────────────────────────


@mcp.tool()
async def node_file_list(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node")],
    path: Annotated[str, Field(description="Subdirectory path within node directory (optional)")] = "",
    recursive: Annotated[bool, Field(description="Recursively list all files (optional, default: false)")] = False,
) -> list[dict[str, Any]]:
    """List files in a node directory with metadata (name, size, type, modified time).

    Use this first to check file sizes before reading files with get_node_file.
    Large config files should be read in chunks using offset/limit.
    """
    return await asyncio.to_thread(_run_handler_sync, list_node_files_handler, {
        "project_id": project_id, "node_id": node_id, "path": path, "recursive": recursive,
    })


@mcp.tool()
async def node_file_get(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node")],
    file_path: Annotated[str, Field(description="Path to the file within the node directory")],
    offset: Annotated[int, Field(description="Line offset to start reading from (optional, default: 0)")] = 0,
    limit: Annotated[int, Field(description="Maximum number of lines to return (optional, default: 200)")] = 200,
) -> list[dict[str, Any]]:
    """Read a text file from a node directory line-by-line with offset/limit support.

    Best practice:
      1. First call list_node_files to see the file size before deciding to read.
      2. Start with offset=0, limit=200 to preview the file.
      3. If metadata.has_more is true, read more by increasing offset.
      Large files (>50KB) are auto-truncated; check the metadata.truncated flag.
      For binary files, check the file type via list_node_files first.
    """
    return await asyncio.to_thread(_run_handler_sync, get_node_file_handler, {
        "project_id": project_id, "node_id": node_id, "file_path": file_path,
        "offset": offset, "limit": limit,
    })


@mcp.tool()
async def node_file_write(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node")],
    file_path: Annotated[str, Field(description="Path to the file within the node directory")],
    content: Annotated[str, Field(description="Content to write to the file")],
) -> list[dict[str, Any]]:
    """Write content to a file in a node directory. Creates the file if it doesn't exist. Overwrites existing content."""
    return await asyncio.to_thread(_run_handler_sync, write_node_file_handler, {
        "project_id": project_id, "node_id": node_id, "file_path": file_path, "content": content,
    })


@mcp.tool()
async def node_file_delete(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node")],
    file_path: Annotated[str, Field(description="Path to the file within the node directory")],
) -> list[dict[str, Any]]:
    """Delete a file from a node directory. Cannot be undone."""
    return await asyncio.to_thread(_run_handler_sync, delete_node_file_handler, {
        "project_id": project_id, "node_id": node_id, "file_path": file_path,
    })


# ── Node bulk / advanced tools ─────────────────────────────────────────


@mcp.tool()
async def node_start_all(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Start all nodes in a project."""
    return await asyncio.to_thread(_run_handler_sync, start_all_nodes_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def node_stop_all(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Stop all nodes in a project."""
    return await asyncio.to_thread(_run_handler_sync, stop_all_nodes_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def node_suspend_all(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Suspend all nodes in a project."""
    return await asyncio.to_thread(_run_handler_sync, suspend_all_nodes_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def node_reload_all(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Reload (restart) all nodes in a project."""
    return await asyncio.to_thread(_run_handler_sync, reload_all_nodes_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def node_duplicate(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to duplicate")],
    x: Annotated[int, Field(description="X coordinate for the new node")] = 0,
    y: Annotated[int, Field(description="Y coordinate for the new node")] = 0,
    z: Annotated[int, Field(description="Z layer for the new node")] = 0,
) -> list[dict[str, Any]]:
    """Duplicate a node in a project, creating a copy at a new position."""
    return await asyncio.to_thread(_run_handler_sync, duplicate_node_handler, {
        "project_id": project_id, "node_id": node_id, "x": x, "y": y, "z": z,
    })


@mcp.tool()
async def node_isolate(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to isolate")],
) -> list[dict[str, Any]]:
    """Isolate a node by suspending all its attached links (network isolation)."""
    return await asyncio.to_thread(_run_handler_sync, isolate_node_handler, {
        "project_id": project_id, "node_id": node_id,
    })


@mcp.tool()
async def node_unisolate(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node to unisolate")],
) -> list[dict[str, Any]]:
    """Un-isolate a node by resuming all its suspended links."""
    return await asyncio.to_thread(_run_handler_sync, unisolate_node_handler, {
        "project_id": project_id, "node_id": node_id,
    })


@mcp.tool()
async def node_links(
    project_id: Annotated[str, Field(description="UUID of the project")],
    node_id: Annotated[str, Field(description="UUID of the node")],
) -> list[dict[str, Any]]:
    """List all links connected to a specific node."""
    return await asyncio.to_thread(_run_handler_sync, get_node_links_handler, {
        "project_id": project_id, "node_id": node_id,
    })


# ── Link capture / reset tools ────────────────────────────────────────


@mcp.tool()
async def link_reset(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link")],
) -> list[dict[str, Any]]:
    """Reset a link, clearing its state (counters, filters, etc.)."""
    return await asyncio.to_thread(_run_handler_sync, reset_link_handler, {
        "project_id": project_id, "link_id": link_id,
    })


@mcp.tool()
async def link_capture_start(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link")],
    data_link_type: Annotated[str, Field(description="Data link type (default: DLT_EN10MB)")] = "DLT_EN10MB",
    capture_file_name: Annotated[str | None, Field(description="Capture file name (optional)")] = None,
    wireshark: Annotated[bool, Field(description="Open Wireshark automatically (default: false)")] = False,
) -> list[dict[str, Any]]:
    """Start packet capture on a link. The capture file can later be downloaded with download_capture_file."""
    return await asyncio.to_thread(_run_handler_sync, start_capture_handler, {
        "project_id": project_id, "link_id": link_id,
        "data_link_type": data_link_type, "capture_file_name": capture_file_name,
        "wireshark": wireshark,
    })


@mcp.tool()
async def link_capture_stop(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link")],
) -> list[dict[str, Any]]:
    """Stop packet capture on a link. After stopping, the capture file can be downloaded."""
    return await asyncio.to_thread(_run_handler_sync, stop_capture_handler, {
        "project_id": project_id, "link_id": link_id,
    })


@mcp.tool()
async def link_capture_download(
    project_id: Annotated[str, Field(description="UUID of the project")],
    link_id: Annotated[str, Field(description="UUID of the link")],
) -> list[dict[str, Any]]:
    """Get the download URL and instructions for a PCAP capture file. Use curl to download."""
    return await asyncio.to_thread(_run_handler_sync, download_capture_file_handler, {
        "project_id": project_id, "link_id": link_id,
    })


# ── Snapshot tools ─────────────────────────────────────────────────────


@mcp.tool()
async def snapshot_list(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """List all snapshots of a project."""
    return await asyncio.to_thread(_run_handler_sync, get_snapshots_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def snapshot_create(
    project_id: Annotated[str, Field(description="UUID of the project")],
    name: Annotated[str, Field(description="Name for the new snapshot")],
) -> list[dict[str, Any]]:
    """Create a new snapshot of a project."""
    return await asyncio.to_thread(_run_handler_sync, create_snapshot_handler, {
        "project_id": project_id, "name": name,
    })


@mcp.tool()
async def snapshot_delete(
    project_id: Annotated[str, Field(description="UUID of the project")],
    snapshot_id: Annotated[str, Field(description="UUID of the snapshot to delete")],
) -> list[dict[str, Any]]:
    """Delete a snapshot from a project. Cannot be undone."""
    return await asyncio.to_thread(_run_handler_sync, delete_snapshot_handler, {
        "project_id": project_id, "snapshot_id": snapshot_id,
    })


@mcp.tool()
async def snapshot_restore(
    project_id: Annotated[str, Field(description="UUID of the project")],
    snapshot_id: Annotated[str, Field(description="UUID of the snapshot to restore")],
) -> list[dict[str, Any]]:
    """Restore a project to a previous snapshot state. The project may be closed and reopened."""
    return await asyncio.to_thread(_run_handler_sync, restore_snapshot_handler, {
        "project_id": project_id, "snapshot_id": snapshot_id,
    })


# ── Drawing tools ──────────────────────────────────────────────────────


@mcp.tool()
async def drawing_list(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """List all drawings (labels, shapes, images) on a project canvas."""
    return await asyncio.to_thread(_run_handler_sync, get_drawings_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def drawing_create(
    project_id: Annotated[str, Field(description="UUID of the project")],
    svg: Annotated[str, Field(description="SVG content for the drawing")],
    x: Annotated[int, Field(description="X coordinate (default: 0)")] = 0,
    y: Annotated[int, Field(description="Y coordinate (default: 0)")] = 0,
    z: Annotated[int, Field(description="Z layer (default: 0)")] = 0,
    locked: Annotated[bool, Field(description="Lock the drawing (default: false)")] = False,
    rotation: Annotated[int, Field(description="Rotation angle in degrees, -359 to 359 (default: 0)")] = 0,
) -> list[dict[str, Any]]:
    """Create a new drawing (label, shape, or image) on a project canvas."""
    return await asyncio.to_thread(_run_handler_sync, create_drawing_handler, {
        "project_id": project_id, "svg": svg, "x": x, "y": y, "z": z,
        "locked": locked, "rotation": rotation,
    })


@mcp.tool()
async def drawing_get(
    project_id: Annotated[str, Field(description="UUID of the project")],
    drawing_id: Annotated[str, Field(description="UUID of the drawing")],
) -> list[dict[str, Any]]:
    """Get detailed information about a specific drawing."""
    return await asyncio.to_thread(_run_handler_sync, get_drawing_handler, {
        "project_id": project_id, "drawing_id": drawing_id,
    })


@mcp.tool()
async def drawing_update(
    project_id: Annotated[str, Field(description="UUID of the project")],
    drawing_id: Annotated[str, Field(description="UUID of the drawing")],
    svg: Annotated[str | None, Field(description="New SVG content")] = None,
    locked: Annotated[bool | None, Field(description="Lock or unlock the drawing")] = None,
    x: Annotated[int | None, Field(description="New X coordinate")] = None,
    y: Annotated[int | None, Field(description="New Y coordinate")] = None,
    z: Annotated[int | None, Field(description="New Z layer")] = None,
) -> list[dict[str, Any]]:
    """Update a drawing's properties (svg, position, lock state, etc.)."""
    params = {"project_id": project_id, "drawing_id": drawing_id}
    local_vars = {"svg": svg, "locked": locked, "x": x, "y": y, "z": z}
    for key, val in local_vars.items():
        if val is not None:
            params[key] = val
    return await asyncio.to_thread(_run_handler_sync, update_drawing_handler, params)


@mcp.tool()
async def drawing_delete(
    project_id: Annotated[str, Field(description="UUID of the project")],
    drawing_id: Annotated[str, Field(description="UUID of the drawing to delete")],
) -> list[dict[str, Any]]:
    """Delete a drawing from a project canvas. Cannot be undone."""
    return await asyncio.to_thread(_run_handler_sync, delete_drawing_handler, {
        "project_id": project_id, "drawing_id": drawing_id,
    })


# ── Project lock tools ────────────────────────────────────────────────


@mcp.tool()
async def project_lock(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Lock all drawings and nodes in a project to prevent accidental changes."""
    return await asyncio.to_thread(_run_handler_sync, lock_project_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def project_unlock(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Unlock a project to allow editing of drawings and nodes."""
    return await asyncio.to_thread(_run_handler_sync, unlock_project_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def project_locked(
    project_id: Annotated[str, Field(description="UUID of the project")],
) -> list[dict[str, Any]]:
    """Check whether a project is locked (preventing edits to drawings and nodes)."""
    return await asyncio.to_thread(_run_handler_sync, get_locked_project_handler, {
        "project_id": project_id,
    })


@mcp.tool()
async def project_load(
    path: Annotated[str, Field(description="Filesystem path to the .gns3 project file")],
) -> list[dict[str, Any]]:
    """Load a project from a file path on the server's filesystem."""
    return await asyncio.to_thread(_run_handler_sync, load_project_handler, {
        "path": path,
    })


# ── Server info tools ─────────────────────────────────────────────────


@mcp.tool()
async def server_version() -> list[dict[str, Any]]:
    """Get GNS3 server version information."""
    return await asyncio.to_thread(_run_handler_sync, get_version_handler, {})


@mcp.tool()
async def server_statistics() -> list[dict[str, Any]]:
    """Get GNS3 server statistics including computes, projects, nodes, and links."""
    return await asyncio.to_thread(_run_handler_sync, get_statistics_handler, {})


# ── Symbol tools ──────────────────────────────────────────────────────


@mcp.tool()
async def symbol_list() -> list[dict[str, Any]]:
    """List all available symbols on the server."""
    return await asyncio.to_thread(_run_handler_sync, get_symbols_handler, {})


@mcp.tool()
async def symbol_get(
    symbol_id: Annotated[str, Field(description="Symbol ID (e.g. ':/symbols/router.svg')")],
) -> list[dict[str, Any]]:
    """Get details about a specific symbol."""
    return await asyncio.to_thread(_run_handler_sync, get_symbol_handler, {
        "symbol_id": symbol_id,
    })


@mcp.tool()
async def symbol_dimensions(
    symbol_id: Annotated[str, Field(description="Symbol ID to get dimensions for")],
) -> list[dict[str, Any]]:
    """Get the dimensions (width, height) of a symbol."""
    return await asyncio.to_thread(_run_handler_sync, get_symbol_dimensions_handler, {
        "symbol_id": symbol_id,
    })


@mcp.tool()
async def symbol_defaults() -> list[dict[str, Any]]:
    """Get the default symbol mapping for each node type."""
    return await asyncio.to_thread(_run_handler_sync, get_default_symbols_handler, {})


@mcp.tool()
async def symbol_upload(
    symbol_id: Annotated[str, Field(description="Symbol ID to upload (e.g. ':/symbols/my_symbol.svg')")],
) -> list[dict[str, Any]]:
    """Upload or update a custom symbol on the server."""
    return await asyncio.to_thread(_run_handler_sync, upload_symbol_handler, {
        "symbol_id": symbol_id,
    })


@mcp.tool()
async def symbol_delete(
    symbol_id: Annotated[str, Field(description="Symbol ID to delete")],
) -> list[dict[str, Any]]:
    """Delete a custom symbol from the server."""
    return await asyncio.to_thread(_run_handler_sync, delete_symbol_handler, {
        "symbol_id": symbol_id,
    })


# ── Appliance tools ───────────────────────────────────────────────────


@mcp.tool()
async def appliance_list() -> list[dict[str, Any]]:
    """List all available appliances (template library)."""
    return await asyncio.to_thread(_run_handler_sync, get_appliances_handler, {})


@mcp.tool()
async def appliance_get(
    appliance_id: Annotated[str, Field(description="UUID of the appliance")],
) -> list[dict[str, Any]]:
    """Get detailed information about a specific appliance."""
    return await asyncio.to_thread(_run_handler_sync, get_appliance_handler, {
        "appliance_id": appliance_id,
    })


@mcp.tool()
async def appliance_install(
    appliance_id: Annotated[str, Field(description="UUID of the appliance to install")],
) -> list[dict[str, Any]]:
    """Install (download and set up) an appliance from the template library."""
    return await asyncio.to_thread(_run_handler_sync, install_appliance_handler, {
        "appliance_id": appliance_id,
    })


# ── Image tools ───────────────────────────────────────────────────────


@mcp.tool()
async def image_list() -> list[dict[str, Any]]:
    """List all images available on the server across all emulators."""
    return await asyncio.to_thread(_run_handler_sync, get_images_handler, {})


@mcp.tool()
async def image_get(
    image_id: Annotated[str, Field(description="ID or filename of the image")],
) -> list[dict[str, Any]]:
    """Get detailed information about a specific image."""
    return await asyncio.to_thread(_run_handler_sync, get_image_handler, {
        "image_id": image_id,
    })


@mcp.tool()
async def image_delete(
    image_id: Annotated[str, Field(description="ID or filename of the image to delete")],
) -> list[dict[str, Any]]:
    """Delete an image from the server. Cannot be undone."""
    return await asyncio.to_thread(_run_handler_sync, delete_image_handler, {
        "image_id": image_id,
    })


@mcp.tool()
async def image_prune() -> list[dict[str, Any]]:
    """Remove all unused images from the server to free up disk space."""
    return await asyncio.to_thread(_run_handler_sync, prune_images_handler, {})


@mcp.tool()
async def image_install() -> list[dict[str, Any]]:
    """Request the server to install pending images (download from registry)."""
    return await asyncio.to_thread(_run_handler_sync, install_images_handler, {})


# ── Device config tools ───────────────────────────────────────────────
# These tools connect to network device consoles via telnet/SSH using
# Nornir + Netmiko. Devices must be started and have a device_type tag.
#
# Workflow:
#   1. node_list(project_id) → identify device names
#   2. node_start_all(project_id) → ensure devices are running
#   3. device_config_send(project_id, device_configs=[...]) → push config
#   4. device_command_run(project_id, device_commands=[...]) → verify


@mcp.tool()
async def device_config_send(
    project_id: Annotated[str, Field(description="UUID of the project")],
    device_configs: Annotated[list, Field(
        description="List of device configs. Each entry: {\"device_name\": \"R1\", \"config_commands\": [\"int lo0\", \"ip add 1.1.1.1 255.255.255.255\"]}"
    )],
) -> list[dict[str, Any]]:
    """Send configuration commands to network devices via console (telnet/SSH).

    Devices must be started first (use node_start or node_start_all).
    Device type is auto-detected from the 'device_type:<type>' tag on each node.
    Common device types: cisco_ios_telnet, cisco_xr_telnet, huawei_telnet, gns3_huawei_telnet_ce
    """
    return await asyncio.to_thread(_run_handler_sync, device_config_send_handler, {
        "project_id": project_id, "device_configs": device_configs,
    })


@mcp.tool()
async def device_command_run(
    project_id: Annotated[str, Field(description="UUID of the project")],
    device_commands: Annotated[list, Field(
        description="List of device show commands. Each entry: {\"device_name\": \"R1\", \"show_commands\": [\"show ip int brief\", \"show running-config\"]}"
    )],
) -> list[dict[str, Any]]:
    """Run read-only diagnostic (show) commands on network devices via console.

    Use this to inspect device status, view configurations, or verify changes.
    Devices must be started first.
    """
    return await asyncio.to_thread(_run_handler_sync, device_command_run_handler, {
        "project_id": project_id, "device_commands": device_commands,
    })


@mcp.tool()
async def vpcs_config_set(
    project_id: Annotated[str, Field(description="UUID of the project")],
    device_configs: Annotated[list, Field(
        description="List of VPCS configs. Each entry: {\"device_name\": \"PC1\", \"commands\": [\"ip 10.0.0.1/24 10.0.0.254\", \"save\"]}"
    )],
) -> list[dict[str, Any]]:
    """Configure VPCS devices (set IP addresses, gateway, etc.).

    VPCS-specific configuration commands:
      - ip <address>/<mask> <gateway>   Set IP and gateway
      - save                            Save config to startup.vpc
      - ping <target>                   Test connectivity
    """
    return await asyncio.to_thread(_run_handler_sync, vpcs_config_set_handler, {
        "project_id": project_id, "device_configs": device_configs,
    })


# ── Auth‑wrapped SSE app ──────────────────────────────────────────────

def _make_auth_wrapper(inner_app):
    """Wrap the SSE app with JWT validation.

    Supports two ways to pass the token (checked in order):
      1. Authorization: Bearer <jwt> header
      2. ?token=<jwt> query parameter

    POST messages are passed through (authenticated by their session).
    """

    async def auth_wrapper(scope, receive, send):
        # Wait for GNS3 server to complete initialization before accepting MCP connections
        server_ready = await wait_for_mcp_ready()
        if not server_ready:
            # Server initialization timed out - return 503 Service Unavailable
            client_info = extract_client_info(scope, auth_service)
            log.warning(
                f"Rejecting MCP connection - GNS3 server initialization not complete. "
                f"Client: {client_info['host']}:{client_info['port']} ({client_info['user_info']}, Path: {client_info['path']})"
            )
            response = Response(
                "GNS3 server initialization not complete - please retry later",
                status_code=503
            )
            await response(scope, receive, send)
            return

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
