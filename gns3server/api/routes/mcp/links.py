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
MCP tool handlers for GNS3 link management.

Handlers receive (params, gns3_ctx) and call GNS3's REST API
via Gns3Connector (from custom_gns3fy).
"""

from typing import Any

import logging

log = logging.getLogger(__name__)


# ── Helper ─────────────────────────────────────────────────────────────────

def _get_connector(gns3_ctx: dict[str, Any]):
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Gns3Connector
    return Gns3Connector(
        url=gns3_ctx["server_url"],
        jwt_token=gns3_ctx["jwt_token"],
        api_version=3,
        verify=False,
    )


# ── Tool handlers ──────────────────────────────────────────────────────────

def get_links_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    links = conn.get_links(project_id=project_id)
    return {"links": links, "count": len(links)}


def get_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    return conn.get_link(project_id=project_id, link_id=link_id)


def create_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    nodes = params.get("nodes")
    if not project_id or not nodes:
        return {"error": "project_id and nodes are required"}
    conn = _get_connector(gns3_ctx)
    data = {"nodes": nodes}
    if "link_type" in params:
        data["link_type"] = params["link_type"]
    if "filters" in params:
        data["filters"] = params["filters"]
    if "suspend" in params:
        data["suspend"] = params["suspend"]
    url = f"{conn.base_url}/projects/{project_id}/links"
    return conn.http_call("post", url, json_data=data).json()


def delete_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    conn.http_call("delete", f"{conn.base_url}/projects/{project_id}/links/{link_id}")
    return {"message": f"Link {link_id} deleted", "link_id": link_id}


def update_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    update_data = {k: v for k, v in params.items() if k not in ("project_id", "link_id")}
    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}"
    return conn.http_call("put", url, json_data=update_data).json()


# ── Tool definitions ───────────────────────────────────────────────────────

LINK_TOOLS = [
    {
        "name": "get_links",
        "description": "List all links in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        "handler": get_links_handler,
    },
    {
        "name": "get_link",
        "description": "Get detailed information about a specific link",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": get_link_handler,
    },
    {
        "name": "create_link",
        "description": "Create a link between two nodes in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "nodes": {
                    "type": "array",
                    "description": "List of node connections, each with node_id, adapter_number, port_number",
                    "items": {
                        "type": "object",
                        "properties": {
                            "node_id": {"type": "string"},
                            "adapter_number": {"type": "integer"},
                            "port_number": {"type": "integer"},
                        },
                    },
                },
                "link_type": {"type": "string", "description": "Link type: ethernet or serial (optional)"},
                "filters": {"type": "object", "description": "Packet filters (optional)"},
            },
            "required": ["project_id", "nodes"],
        },
        "handler": create_link_handler,
    },
    {
        "name": "delete_link",
        "description": "Delete a link from a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": delete_link_handler,
    },
    {
        "name": "update_link",
        "description": "Update a link's properties (suspend, filters, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
                "suspend": {"type": "boolean", "description": "Suspend the link (optional)"},
                "filters": {"type": "object", "description": "Packet filters (optional)"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": update_link_handler,
    },
]
