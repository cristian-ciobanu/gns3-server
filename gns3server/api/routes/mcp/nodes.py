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
MCP tool handlers for GNS3 node management.

Handlers receive (params, gns3_ctx) and call GNS3's REST API
via Gns3Connector (from custom_gns3fy).
"""

from typing import Any

import logging

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

# Maximum bytes to return from get_node_file (safety net).
# Larger files are truncated with a truncated=True flag.
MAX_NODE_FILE_BYTES = 50 * 1024  # 50 KiB


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

def get_nodes_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    nodes = conn.get_nodes(project_id=project_id)
    return {"nodes": nodes, "count": len(nodes)}


def get_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    return conn.get_node(project_id=project_id, node_id=node_id)


def start_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    conn.http_call("post", f"{conn.base_url}/projects/{project_id}/nodes/{node_id}/start", json_data={})
    return {"message": f"Node {node_id} started", "node_id": node_id}


def stop_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    conn.http_call("post", f"{conn.base_url}/projects/{project_id}/nodes/{node_id}/stop", json_data={})
    return {"message": f"Node {node_id} stopped", "node_id": node_id}


def reload_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    conn.http_call("post", f"{conn.base_url}/projects/{project_id}/nodes/{node_id}/reload")
    return {"message": f"Node {node_id} reloaded", "node_id": node_id}


def suspend_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    conn.http_call("post", f"{conn.base_url}/projects/{project_id}/nodes/{node_id}/suspend")
    return {"message": f"Node {node_id} suspended", "node_id": node_id}


def create_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    template_id = params.get("template_id")
    if not project_id or not template_id:
        return {"error": "project_id and template_id are required"}
    conn = _get_connector(gns3_ctx)
    data = {
        "x": params.get("x", 0),
        "y": params.get("y", 0),
        "compute_id": params.get("compute_id", "local"),
    }
    url = f"{conn.base_url}/projects/{project_id}/templates/{template_id}"
    return conn.http_call("post", url, json_data=data).json()


def delete_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    conn.http_call("delete", f"{conn.base_url}/projects/{project_id}/nodes/{node_id}")
    return {"message": f"Node {node_id} deleted", "node_id": node_id}


def update_node_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)

    # Extract update parameters - handle nested kwargs structure from MCP clients
    if "kwargs" in params and isinstance(params["kwargs"], dict):
        update_data = params["kwargs"]
    else:
        update_data = {k: v for k, v in params.items() if k not in ("project_id", "node_id", "kwargs")}

    url = f"{conn.base_url}/projects/{project_id}/nodes/{node_id}"
    return conn.http_call("put", url, json_data=update_data).json()


def get_node_console_info_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    node = conn.get_node(project_id=project_id, node_id=node_id)

    console_type = node.get("console_type", "unknown")
    ws_url = f"{gns3_ctx['server_url']}/v3/projects/{project_id}/nodes/{node_id}/console/ws?token={gns3_ctx['jwt_token']}"

    result = {
        "node_id": node_id,
        "node_name": node.get("name"),
        "console_type": console_type,
        "ws_url": ws_url,
        "command": f"websocat {ws_url}",
    }
    if console_type in ("vnc",):
        result["vnc_url"] = f"/v3/projects/{project_id}/nodes/{node_id}/console/vnc?token={gns3_ctx['jwt_token']}"
    return result


# ── Node file handlers ────────────────────────────────────────────────────


def list_node_files_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    if not project_id or not node_id:
        return {"error": "project_id and node_id are required"}
    conn = _get_connector(gns3_ctx)
    files = conn.list_node_files(
        project_id=project_id,
        node_id=node_id,
        path=params.get("path", ""),
        recursive=params.get("recursive", False),
    )
    return {"files": files, "count": len(files)}


def get_node_file_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    file_path = params.get("file_path")
    if not project_id or not node_id or not file_path:
        return {"error": "project_id, node_id and file_path are required"}

    offset = params.get("offset", 0)
    limit = params.get("limit", 200)

    conn = _get_connector(gns3_ctx)
    raw = conn.get_node_file(project_id=project_id, node_id=node_id, file_path=file_path)

    total_bytes = len(raw.encode("utf-8"))
    truncated = False
    if total_bytes > MAX_NODE_FILE_BYTES:
        raw = raw[:MAX_NODE_FILE_BYTES]
        truncated = True

    lines = raw.splitlines(keepends=False)
    total_lines = len(lines)

    # Apply offset/limit
    selected = lines[offset: offset + limit] if offset < total_lines else []
    has_more = (offset + limit) < total_lines or truncated

    return {
        "file_path": file_path,
        "content": "\n".join(selected),
        "metadata": {
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "offset": offset,
            "limit": limit,
            "returned_lines": len(selected),
            "returned_bytes": len("\n".join(selected).encode("utf-8")),
            "truncated": truncated or has_more,
            "has_more": has_more,
        },
    }


def write_node_file_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    file_path = params.get("file_path")
    content = params.get("content")
    if not project_id or not node_id or not file_path or content is None:
        return {"error": "project_id, node_id, file_path and content are required"}
    conn = _get_connector(gns3_ctx)
    conn.write_node_file(project_id=project_id, node_id=node_id, file_path=file_path, content=content)
    return {"message": f"File {file_path} written to node {node_id}", "file_path": file_path, "node_id": node_id}


def delete_node_file_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    node_id = params.get("node_id")
    file_path = params.get("file_path")
    if not project_id or not node_id or not file_path:
        return {"error": "project_id, node_id and file_path are required"}
    conn = _get_connector(gns3_ctx)
    conn.delete_node_file(project_id=project_id, node_id=node_id, file_path=file_path)
    return {"message": f"File {file_path} deleted from node {node_id}", "file_path": file_path, "node_id": node_id}


# ── Tool definitions ───────────────────────────────────────────────────────

NODE_TOOLS = [
    {
        "name": "get_nodes",
        "description": "List all nodes in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        "handler": get_nodes_handler,
    },
    {
        "name": "get_node",
        "description": "Get detailed information about a specific node",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": get_node_handler,
    },
    {
        "name": "start_node",
        "description": "Start a node in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": start_node_handler,
    },
    {
        "name": "stop_node",
        "description": "Stop a node in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": stop_node_handler,
    },
    {
        "name": "reload_node",
        "description": "Reload (restart) a node in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": reload_node_handler,
    },
    {
        "name": "suspend_node",
        "description": "Suspend a node in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": suspend_node_handler,
    },
    {
        "name": "create_node",
        "description": "Create a new node from a template in a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "template_id": {"type": "string", "description": "Template UUID"},
                "x": {"type": "integer", "description": "X coordinate (optional)"},
                "y": {"type": "integer", "description": "Y coordinate (optional)"},
                "compute_id": {"type": "string", "description": "Compute ID (optional, default: local)"},
            },
            "required": ["project_id", "template_id"],
        },
        "handler": create_node_handler,
    },
    {
        "name": "delete_node",
        "description": "Delete a node from a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": delete_node_handler,
    },
    {
        "name": "update_node",
        "description": "Update a node's properties (name, position, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
                "name": {"type": "string", "description": "New node name (optional)"},
                "x": {"type": "integer", "description": "New X position (optional)"},
                "y": {"type": "integer", "description": "New Y position (optional)"},
                "compute_id": {"type": "string", "description": "Compute ID (optional)"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": update_node_handler,
    },
    {
        "name": "get_node_console_info",
        "description": "Get console WebSocket URL for a node (use websocat to connect)",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": get_node_console_info_handler,
    },
    {
        "name": "list_node_files",
        "description": "List files in a node directory with metadata (name, size, type, modified time). "
                       "Use recursive=true for a full recursive listing. "
                       "Check file sizes before reading large files with get_node_file.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
                "path": {"type": "string", "description": "Subdirectory path within node directory (optional)"},
                "recursive": {"type": "boolean", "description": "Recursively list all files (optional, default: false)"},
            },
            "required": ["project_id", "node_id"],
        },
        "handler": list_node_files_handler,
    },
    {
        "name": "get_node_file",
        "description": "Read a text file from a node directory. Returns file content line-by-line with offset/limit support. "
                       "Best practice: start with offset=0, limit=200 to preview, then increase offset to read more. "
                       "Large files (>50KB) are auto-truncated; check the metadata.truncated flag. "
                       "For binary files, check file type via list_node_files first.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
                "file_path": {"type": "string", "description": "Path to the file within the node directory"},
                "offset": {"type": "integer", "description": "Line offset to start reading from (optional, default: 0)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to return (optional, default: 200)"},
            },
            "required": ["project_id", "node_id", "file_path"],
        },
        "handler": get_node_file_handler,
    },
    {
        "name": "write_node_file",
        "description": "Write content to a file in a node directory. Creates the file if it doesn't exist. "
                       "Overwrites existing content. Useful for updating configuration files on nodes.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
                "file_path": {"type": "string", "description": "Path to the file within the node directory"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["project_id", "node_id", "file_path", "content"],
        },
        "handler": write_node_file_handler,
    },
    {
        "name": "delete_node_file",
        "description": "Delete a file from a node directory. Cannot be undone. "
                       "Use list_node_files to confirm the file path before deleting.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "node_id": {"type": "string", "description": "Node UUID"},
                "file_path": {"type": "string", "description": "Path to the file within the node directory"},
            },
            "required": ["project_id", "node_id", "file_path"],
        },
        "handler": delete_node_file_handler,
    },
]
