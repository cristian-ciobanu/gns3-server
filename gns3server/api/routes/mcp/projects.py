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

Each handler receives (parameters, current_user, jwt_token, server_url) and
uses a Gns3Connector (from custom_gns3fy) to call GNS3's own REST API.
This keeps the MCP layer decoupled from the controller internals.
"""

from typing import Dict, Any

from . import registry, MCPTool

import logging

log = logging.getLogger(__name__)


# ── Helper ─────────────────────────────────────────────────────────────────

def _get_connector(server_url: str, jwt_token: str):
    """Create a Gns3Connector using the user's JWT token."""
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Gns3Connector
    return Gns3Connector(
        url=server_url,
        jwt_token=jwt_token,
        api_version=3,
        verify=False,
    )


# ── Tool: list_projects ────────────────────────────────────────────────────

def list_projects_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Return all projects via GNS3 REST API."""
    conn = _get_connector(server_url, jwt_token)
    projects = conn.get_projects()
    return {"projects": projects, "count": len(projects)}


# ── Tool: get_project ──────────────────────────────────────────────────────

def get_project_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Return a single project by project_id."""
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    conn = _get_connector(server_url, jwt_token)
    project = conn.get_project(project_id=project_id)
    if project is None:
        return {"error": f"Project '{project_id}' not found"}
    return project


# ── Tool: create_project ───────────────────────────────────────────────────

def create_project_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Create a new project via GNS3 REST API."""
    name = params.get("name")
    if not name:
        return {"error": "name is required"}

    conn = _get_connector(server_url, jwt_token)
    project_data = {"name": name}
    if "description" in params:
        project_data["description"] = params["description"]

    project = conn.create_project(**project_data)
    return project


# ── Tool: delete_project ───────────────────────────────────────────────────

def delete_project_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Delete a project by project_id via GNS3 REST API."""
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    conn = _get_connector(server_url, jwt_token)
    conn.delete_project(project_id=project_id)
    return {"message": f"Project '{project_id}' deleted", "project_id": project_id}


# ── Tool: open_project ─────────────────────────────────────────────────────

def open_project_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Open a closed project via GNS3 REST API."""
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    conn = _get_connector(server_url, jwt_token)
    url = f"{conn.base_url}/projects/{project_id}/open"
    response = conn.http_call("post", url)
    return response.json()


# ── Tool: close_project ────────────────────────────────────────────────────

def close_project_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Close an open project via GNS3 REST API."""
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    conn = _get_connector(server_url, jwt_token)
    url = f"{conn.base_url}/projects/{project_id}/close"
    conn.http_call("post", url)
    return {"message": f"Project '{project_id}' closed", "project_id": project_id}


# ── Tool: get_project_stats ────────────────────────────────────────────────

def get_project_stats_handler(
    params: Dict[str, Any],
    current_user=None,
    jwt_token=None,
    server_url=None,
) -> Dict[str, Any]:
    """Return project statistics via GNS3 REST API."""
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    conn = _get_connector(server_url, jwt_token)
    url = f"{conn.base_url}/projects/{project_id}/stats"
    response = conn.http_call("get", url)
    return response.json()


# ── Register all project tools ─────────────────────────────────────────────

def register_tools():
    """Register every project-related MCP tool into the global registry."""
    tools = [
        MCPTool(
            name="list_projects",
            description="List all GNS3 projects accessible to the current user",
            parameters_schema={"type": "object", "properties": {}},
            handler=list_projects_handler,
        ),
        MCPTool(
            name="get_project",
            description="Get detailed information about a specific project",
            parameters_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project UUID",
                    }
                },
                "required": ["project_id"],
            },
            handler=get_project_handler,
        ),
        MCPTool(
            name="create_project",
            description="Create a new GNS3 project",
            parameters_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "description": {
                        "type": "string",
                        "description": "Optional project description",
                    },
                },
                "required": ["name"],
            },
            handler=create_project_handler,
        ),
        MCPTool(
            name="delete_project",
            description="Delete a GNS3 project permanently",
            parameters_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "UUID of the project to delete",
                    }
                },
                "required": ["project_id"],
            },
            handler=delete_project_handler,
        ),
        MCPTool(
            name="open_project",
            description="Open a closed GNS3 project",
            parameters_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project UUID",
                    }
                },
                "required": ["project_id"],
            },
            handler=open_project_handler,
        ),
        MCPTool(
            name="close_project",
            description="Close an open GNS3 project",
            parameters_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project UUID",
                    }
                },
                "required": ["project_id"],
            },
            handler=close_project_handler,
        ),
        MCPTool(
            name="get_project_stats",
            description="Get statistics (nodes, links, snapshots, drawings) for a project",
            parameters_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project UUID",
                    }
                },
                "required": ["project_id"],
            },
            handler=get_project_stats_handler,
        ),
    ]

    for tool in tools:
        registry.register_tool(tool)


# Auto-register on import
register_tools()
