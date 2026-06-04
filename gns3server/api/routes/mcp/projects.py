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
MCP tools for GNS3 project management.

Tool handlers receive (params, gns3_ctx) and call GNS3's REST API
via Gns3Connector (from custom_gns3fy).
"""

from typing import Any

import logging

log = logging.getLogger(__name__)


# ── Helper ─────────────────────────────────────────────────────────────────

def _get_connector(gns3_ctx: dict[str, Any]):
    """Create a Gns3Connector from the GNS3 context dict."""
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Gns3Connector
    return Gns3Connector(
        url=gns3_ctx["server_url"],
        jwt_token=gns3_ctx["jwt_token"],
        api_version=3,
        verify=False,
    )


# ── Tool handlers ──────────────────────────────────────────────────────────

def list_projects_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    conn = _get_connector(gns3_ctx)
    projects = conn.get_projects()
    return {"projects": projects, "count": len(projects)}


def get_project_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    project = conn.get_project(project_id=project_id)
    if project is None:
        return {"error": f"Project '{project_id}' not found"}
    return project


def create_project_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not name:
        return {"error": "name is required"}
    conn = _get_connector(gns3_ctx)
    project_data = {"name": name}
    if "description" in params:
        project_data["description"] = params["description"]
    return conn.create_project(**project_data)


def delete_project_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    conn.delete_project(project_id=project_id)
    return {"message": f"Project '{project_id}' deleted", "project_id": project_id}


def open_project_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/open"
    return conn.http_call("post", url).json()


def close_project_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/close"
    conn.http_call("post", url)
    return {"message": f"Project '{project_id}' closed", "project_id": project_id}


def get_project_stats_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/stats"
    return conn.http_call("get", url).json()


# ── Tool definitions (consumed by mcp/__init__.py) ─────────────────────────

PROJECT_TOOLS = [
    {
        "name": "list_projects",
        "description": "List all GNS3 projects accessible to the current user",
        "parameters": {"type": "object", "properties": {}},
        "handler": list_projects_handler,
    },
    {
        "name": "get_project",
        "description": "Get detailed information about a specific project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        "handler": get_project_handler,
    },
    {
        "name": "create_project",
        "description": "Create a new GNS3 project",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "description": {"type": "string", "description": "Optional project description"},
            },
            "required": ["name"],
        },
        "handler": create_project_handler,
    },
    {
        "name": "delete_project",
        "description": "Delete a GNS3 project permanently",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project to delete"},
            },
            "required": ["project_id"],
        },
        "handler": delete_project_handler,
    },
    {
        "name": "open_project",
        "description": "Open a closed GNS3 project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        "handler": open_project_handler,
    },
    {
        "name": "close_project",
        "description": "Close an open GNS3 project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        "handler": close_project_handler,
    },
    {
        "name": "get_project_stats",
        "description": "Get statistics (nodes, links, snapshots, drawings) for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        "handler": get_project_stats_handler,
    },
]
