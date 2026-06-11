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
    links = conn.http_call("get", f"{conn.base_url}/projects/{project_id}/links").json()
    return {"links": links, "count": len(links)}


def get_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    return conn.http_call("get", f"{conn.base_url}/projects/{project_id}/links/{link_id}").json()


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

    # Extract update parameters - handle nested kwargs structure from MCP clients
    if "kwargs" in params and isinstance(params["kwargs"], dict):
        update_data = params["kwargs"]
    else:
        update_data = {k: v for k, v in params.items() if k not in ("project_id", "link_id", "kwargs")}

    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}"
    return conn.http_call("put", url, json_data=update_data).json()


# ── Link capture / reset handlers ──────────────────────────────────────


def reset_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}/reset"
    result = conn.http_call("post", url).json()
    return {"message": f"Link {link_id} reset", "link": result}


def start_capture_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    data = {
        "data_link_type": params.get("data_link_type", "DLT_EN10MB"),
        "wireshark": params.get("wireshark", False),
    }
    if params.get("capture_file_name"):
        data["capture_file_name"] = params["capture_file_name"]
    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}/capture/start"
    result = conn.http_call("post", url, json_data=data).json()
    return {"message": f"Capture started on link {link_id}", "link": result}


def stop_capture_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}/capture/stop"
    conn.http_call("post", url)
    return {"message": f"Capture stopped on link {link_id}", "link_id": link_id}


def download_capture_file_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    link_id = params.get("link_id")
    if not project_id or not link_id:
        return {"error": "project_id and link_id are required"}
    download_url = f"{gns3_ctx['server_url']}/v3/projects/{project_id}/links/{link_id}/capture/file"
    auth_token = gns3_ctx['jwt_token']
    return {
        "link_id": link_id,
        "download_url": download_url,
        "curl_command": f"curl -L -o capture.pcap -H 'Authorization: Bearer {auth_token}' '{download_url}'",
        "note": "Use the curl command to download the PCAP capture file. "
                "The file is in pcap format and can be analyzed with Wireshark or tcpdump.",
    }


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
                "filters": {
                    "type": "object",
                    "description": "Packet filters (optional). Must use array format: frequency_drop: [N], packet_loss: [rate], delay: [ms, jitter], corrupt: [rate], bpf: [expression]"
                },
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
                "filters": {
                    "type": "object",
                    "description": "Packet filters (optional). Must use array format: frequency_drop: [N], packet_loss: [rate], delay: [ms, jitter], corrupt: [rate], bpf: [expression]. Example: {\"frequency_drop\": [10], \"packet_loss\": [5]}"
                },
            },
            "required": ["project_id", "link_id"],
        },
        "handler": update_link_handler,
    },
    {
        "name": "reset_link",
        "description": "Reset a link, clearing its state (counters, filters, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": reset_link_handler,
    },
    {
        "name": "start_capture",
        "description": "Start packet capture on a link. The capture file can later be downloaded with download_capture_file.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
                "data_link_type": {"type": "string", "description": "Data link type (optional, default: DLT_EN10MB)"},
                "capture_file_name": {"type": "string", "description": "Capture file name (optional)"},
                "wireshark": {"type": "boolean", "description": "Open Wireshark automatically (optional, default: false)"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": start_capture_handler,
    },
    {
        "name": "stop_capture",
        "description": "Stop packet capture on a link. After stopping, the capture file can be downloaded.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": stop_capture_handler,
    },
    {
        "name": "download_capture_file",
        "description": "Get the download URL and instructions for a PCAP capture file from a link. "
                       "Use the returned curl command to download the file. "
                       "The PCAP file can be analyzed with Wireshark or tcpdump.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "link_id": {"type": "string", "description": "Link UUID"},
            },
            "required": ["project_id", "link_id"],
        },
        "handler": download_capture_file_handler,
    },
]
