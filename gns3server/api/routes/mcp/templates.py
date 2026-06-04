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
MCP tool handlers for GNS3 template management.

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

def list_templates_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    conn = _get_connector(gns3_ctx)
    templates = conn.get_templates()
    return {"templates": templates, "count": len(templates)}


def get_template_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    template_id = params.get("template_id")
    name = params.get("name")
    if not template_id and not name:
        return {"error": "template_id or name is required"}
    conn = _get_connector(gns3_ctx)
    template = conn.get_template(name=name, template_id=template_id)
    if template is None:
        return {"error": "Template not found"}
    return template


def create_template_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    template_type = params.get("template_type")
    if not name or not template_type:
        return {"error": "name and template_type are required"}
    conn = _get_connector(gns3_ctx)
    return conn.create_template(**params)


def update_template_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    template_id = params.get("template_id")
    name = params.get("name")
    if not template_id and not name:
        return {"error": "template_id or name is required"}
    conn = _get_connector(gns3_ctx)
    return conn.update_template(name=name, template_id=template_id, **{
        k: v for k, v in params.items() if k not in ("template_id", "name")
    })


def delete_template_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> dict[str, Any]:
    template_id = params.get("template_id")
    name = params.get("name")
    if not template_id and not name:
        return {"error": "template_id or name is required"}
    conn = _get_connector(gns3_ctx)
    conn.delete_template(name=name, template_id=template_id)
    return {"message": f"Template deleted"}


# ── Tool definitions ───────────────────────────────────────────────────────

TEMPLATE_TOOLS = [
    {
        "name": "list_templates",
        "description": "List all available templates on the server",
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "handler": list_templates_handler,
    },
    {
        "name": "get_template",
        "description": "Get detailed information about a specific template",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template UUID"},
                "name": {"type": "string", "description": "Template name"},
            },
        },
        "handler": get_template_handler,
    },
    {
        "name": "create_template",
        "description": "Create a new template",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name"},
                "template_type": {"type": "string", "description": "Template type (e.g. qemu, docker, dynamips)"},
                "compute_id": {"type": "string", "description": "Compute ID (optional, default: local)"},
            },
            "required": ["name", "template_type"],
        },
        "handler": create_template_handler,
    },
    {
        "name": "update_template",
        "description": "Update an existing template's properties",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template UUID"},
                "name": {"type": "string", "description": "Template name"},
            },
        },
        "handler": update_template_handler,
    },
    {
        "name": "delete_template",
        "description": "Delete a template",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template UUID"},
                "name": {"type": "string", "description": "Template name"},
            },
        },
        "handler": delete_template_handler,
    },
]
