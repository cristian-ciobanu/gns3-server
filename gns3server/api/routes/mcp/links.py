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
from concurrent.futures import ThreadPoolExecutor, as_completed

import logging

from gns3server.services import auth_service

log = logging.getLogger(__name__)

BATCH_MAX_WORKERS = 100


# ── Helper ─────────────────────────────────────────────────────────────────

def _get_connector(gns3_ctx: dict[str, Any]):
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Gns3Connector
    return Gns3Connector(
        url=gns3_ctx["server_url"],
        jwt_token=gns3_ctx["jwt_token"],
        api_version=3,
        verify=False,
    )


def _normalize_link_nodes(nodes) -> list[dict[str, Any]]:
    """
    Normalize link node entries, accepting both standard object format and
    compact array format to reduce token usage.

    Standard: [{"node_id": "uuid", "adapter_number": 0, "port_number": 0}]
    Compact:  ["uuid", 0, 0, "uuid", 0, 0]

    Returns the normalized list, or raises ValueError with a clear message
    on format errors so the AI can self-correct.
    """
    if not nodes:
        return nodes
    if not isinstance(nodes, list):
        raise ValueError(f"nodes must be a list, got {type(nodes).__name__}: {nodes}")
    # Standard object format: [{"node_id": "...", ...}]
    if isinstance(nodes[0], dict):
        return nodes
    # Compact array format: ["uuid", ad, pt, "uuid", ad, pt]
    if all(not isinstance(n, dict) for n in nodes):
        if len(nodes) != 6:
            raise ValueError(
                f"Compact link format requires exactly 6 elements "
                f"[node_id, adapter, port, node_id, adapter, port], "
                f"but got {len(nodes)} elements: {nodes}"
            )
        if not isinstance(nodes[0], str) or not isinstance(nodes[3], str):
            raise ValueError(
                f"Compact link format expects node_id (string) at positions 0 and 3, "
                f"got types {type(nodes[0]).__name__} and {type(nodes[3]).__name__}: {nodes}"
            )
        return [
            {"node_id": nodes[0], "adapter_number": nodes[1], "port_number": nodes[2]},
            {"node_id": nodes[3], "adapter_number": nodes[4], "port_number": nodes[5]},
        ]
    raise ValueError(
        f"Unrecognized link nodes format. "
        f"Use standard [{{\"node_id\":\"..\",\"adapter_number\":0,\"port_number\":0}},...] "
        f"or compact [\"id\",0,0,\"id\",0,0], got: {nodes}"
    )


# ── Tool handlers ──────────────────────────────────────────────────────────

VALID_LINK_FIELDS = {
    "link_id", "project_id", "link_type", "nodes", "suspend",
    "link_style", "filters", "show_filters_icon",
    "capturing", "capture_file_name", "capture_file_path",
    "capture_compute_id", "wireshark",
}


LINK_DEFAULT_FIELDS = ["link_id", "link_type", "nodes"]


def _filter_link_response(link: dict, fields: list[str] = None) -> dict:
    """Filter link response to only include requested fields."""
    if not fields:
        fields = LINK_DEFAULT_FIELDS
    return {k: link[k] for k in fields if k in link}


def get_links_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    conn = _get_connector(gns3_ctx)
    links = conn.http_call("get", f"{conn.base_url}/projects/{project_id}/links").json()
    fields = params.get("fields")
    if fields:
        if not isinstance(fields, list):
            return {"error": "fields must be a list, e.g. [\"link_id\", \"nodes\"]"}
        invalid = [f for f in fields if f not in VALID_LINK_FIELDS]
        if invalid:
            return {
                "error": f"Unknown fields: {invalid}",
                "available_fields": sorted(VALID_LINK_FIELDS),
            }
        links = [{k: l[k] for k in fields if k in l} for l in links]
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
    if not project_id:
        return {"error": "project_id is required"}

    fields = params.get("fields")
    if fields is not None and not isinstance(fields, list):
        return {"error": "fields must be a list, e.g. [\"link_id\", \"nodes\"]"}

    links = params.get("links")
    # Batch mode: links=[{nodes, link_type?, filters?, suspend?}]
    if links is not None:
        if not isinstance(links, list) or not links:
            return {"error": "links must be a non-empty array"}
        results = []
        conn = _get_connector(gns3_ctx)
        def _create_one(link_data):
            raw_nodes = link_data.get("nodes")
            if not raw_nodes:
                return {"status": "error", "error": "nodes is required for each link"}
            try:
                body = {"nodes": _normalize_link_nodes(raw_nodes)}
                if link_data.get("link_type"):
                    body["link_type"] = link_data["link_type"]
                if link_data.get("filters"):
                    body["filters"] = link_data["filters"]
                if link_data.get("suspend"):
                    body["suspend"] = link_data["suspend"]
                url = f"{conn.base_url}/projects/{project_id}/links"
                resp = conn.http_call("post", url, json_data=body).json()
                return {"status": "success", "link": _filter_link_response(resp, fields)}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        with ThreadPoolExecutor(max_workers=min(len(links), BATCH_MAX_WORKERS)) as pool:
            futures = {pool.submit(_create_one, l): l for l in links}
            for future in as_completed(futures):
                results.append(future.result())
        return results

    # Single mode
    nodes = params.get("nodes")
    if not nodes:
        return {"error": "nodes is required"}
    conn = _get_connector(gns3_ctx)
    data = {"nodes": _normalize_link_nodes(nodes)}
    if "link_type" in params:
        data["link_type"] = params["link_type"]
    if "filters" in params:
        data["filters"] = params["filters"]
    if "suspend" in params:
        data["suspend"] = params["suspend"]
    url = f"{conn.base_url}/projects/{project_id}/links"
    resp = conn.http_call("post", url, json_data=data).json()
    return _filter_link_response(resp, fields)


def delete_link_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    link_ids = params.get("link_ids")
    if link_ids:
        if not isinstance(link_ids, list):
            return {"error": "link_ids must be a list"}
        conn = _get_connector(gns3_ctx)
        def _del(lid):
            try:
                conn.http_call("delete", f"{conn.base_url}/projects/{project_id}/links/{lid}")
                return {"link_id": lid, "status": "deleted"}
            except Exception as e:
                return {"link_id": lid, "status": "error", "error": str(e)}
        with ThreadPoolExecutor(max_workers=min(len(link_ids), BATCH_MAX_WORKERS)) as pool:
            return list(pool.map(_del, link_ids))
    link_id = params.get("link_id")
    if not link_id:
        return {"error": "link_id or link_ids is required"}
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
    if not project_id:
        return {"error": "project_id is required"}
    link_ids = params.get("link_ids")
    if link_ids:
        if not isinstance(link_ids, list):
            return {"error": "link_ids must be a list"}
        conn = _get_connector(gns3_ctx)
        def _rst(lid):
            try:
                url = f"{conn.base_url}/projects/{project_id}/links/{lid}/reset"
                r = conn.http_call("post", url).json()
                return {"link_id": lid, "status": "reset", "link": r}
            except Exception as e:
                return {"link_id": lid, "status": "error", "error": str(e)}
        with ThreadPoolExecutor(max_workers=min(len(link_ids), BATCH_MAX_WORKERS)) as pool:
            return list(pool.map(_rst, link_ids))
    link_id = params.get("link_id")
    if not link_id:
        return {"error": "link_id or link_ids is required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}/reset"
    result = conn.http_call("post", url).json()
    return {"message": f"Link {link_id} reset", "link": result}


def _batch_capture(project_id, link_ids, action, data_builder, conn):
    """Helper for batch capture start/stop."""
    def _act(lid):
        try:
            url = f"{conn.base_url}/projects/{project_id}/links/{lid}/capture/{action}"
            kwargs = data_builder(lid) if data_builder else {}
            conn.http_call("post", url, **kwargs)
            return {"link_id": lid, "status": "success"}
        except Exception as e:
            return {"link_id": lid, "status": "error", "error": str(e)}
    with ThreadPoolExecutor(max_workers=min(len(link_ids), BATCH_MAX_WORKERS)) as pool:
        return list(pool.map(_act, link_ids))


def start_capture_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    link_ids = params.get("link_ids")
    if link_ids:
        if not isinstance(link_ids, list):
            return {"error": "link_ids must be a list"}
        conn = _get_connector(gns3_ctx)
        dlt = params.get("data_link_type", "DLT_EN10MB")
        ws = params.get("wireshark", False)
        fname = params.get("capture_file_name")
        def _build(lid):
            data = {"data_link_type": dlt, "wireshark": ws}
            if fname:
                data["capture_file_name"] = fname
            return {"json_data": data}
        return _batch_capture(project_id, link_ids, "start", _build, conn)
    link_id = params.get("link_id")
    if not link_id:
        return {"error": "link_id or link_ids is required"}
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
    if not project_id:
        return {"error": "project_id is required"}
    link_ids = params.get("link_ids")
    if link_ids:
        if not isinstance(link_ids, list):
            return {"error": "link_ids must be a list"}
        conn = _get_connector(gns3_ctx)
        return _batch_capture(project_id, link_ids, "stop", None, conn)
    link_id = params.get("link_id")
    if not link_id:
        return {"error": "link_id or link_ids is required"}
    conn = _get_connector(gns3_ctx)
    url = f"{conn.base_url}/projects/{project_id}/links/{link_id}/capture/stop"
    conn.http_call("post", url)
    return {"message": f"Capture stopped on link {link_id}", "link_id": link_id}


def download_capture_file_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    username = gns3_ctx.get("jwt_username")
    download_token = auth_service.create_access_token(username, token_version=gns3_ctx.get("jwt_token_version", 0), expires_in=10) if username else None

    link_ids = params.get("link_ids")
    if link_ids:
        if not isinstance(link_ids, list):
            return {"error": "link_ids must be a list"}
        results = []
        for lid in link_ids:
            url = f"{gns3_ctx['server_url']}/v3/projects/{project_id}/links/{lid}/capture/file"
            entry = {"link_id": lid, "download_url": url}
            if download_token:
                cmd = f"curl -L -o capture_{lid}.pcap -H 'Authorization: Bearer {download_token}' '{url}'"
                entry["curl_command"] = cmd
            results.append(entry)
        return {"downloads": results, "count": len(results), "note": "Files are in pcap format. Links include a 10-minute token."}

    link_id = params.get("link_id")
    if not link_id:
        return {"error": "link_id or link_ids is required"}
    download_url = f"{gns3_ctx['server_url']}/v3/projects/{project_id}/links/{link_id}/capture/file"
    result = {
        "link_id": link_id,
        "download_url": download_url,
        "note": "The file is in pcap format and can be analyzed with Wireshark or tcpdump.",
    }
    if download_token:
        result["curl_command"] = f"curl -L -o capture.pcap -H 'Authorization: Bearer {download_token}' '{download_url}'"
        result["note"] += " The download link includes a 10-minute token."
    return result


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
