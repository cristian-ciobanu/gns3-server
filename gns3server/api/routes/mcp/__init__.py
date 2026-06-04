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
MCP (Model Context Protocol) service routes for GNS3 server.

Provides a unified tool execution interface that wraps existing GNS3 API
functionality. Tools are registered via MCPToolRegistry and executed
through a single POST /v3/mcp/execute endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Dict, Any, List, Callable, Optional
from pydantic import BaseModel
import asyncio
import logging

from gns3server import schemas
from gns3server.api.routes.controller.dependencies.authentication import get_current_active_user
from gns3server.config import Config

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["MCP"])


# ── Tool Registration ──────────────────────────────────────────────────────

class MCPTool:
    """
    An MCP tool binds a name, description, parameter schema, and handler together.
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        handler: Callable,
        required_permission: Optional[str] = None,
    ):
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.handler = handler
        self.required_permission = required_permission

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }


class MCPToolRegistry:
    """Central registry — tools are registered once, listed/executed on demand."""

    def __init__(self):
        self._tools: Dict[str, MCPTool] = {}

    def register_tool(self, tool: MCPTool) -> None:
        self._tools[tool.name] = tool
        log.info(f"Registered MCP tool: {tool.name}")

    def get_tool(self, name: str) -> Optional[MCPTool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        return [t.as_dict() for t in self._tools.values()]

    async def execute(
        self, tool_name: str, parameters: Dict[str, Any], **context
    ) -> Dict[str, Any]:
        tool = self.get_tool(tool_name)
        if tool is None:
            return {"status": "error", "error": f"Tool '{tool_name}' not found"}

        try:
            # Handlers use synchronous Gns3Connector (requests library),
            # so run them in a thread to avoid blocking the event loop.
            result = await asyncio.to_thread(tool.handler, parameters, **context)
            return {"status": "success", "data": result}
        except Exception as e:
            log.error(f"Error executing tool '{tool_name}': {e}")
            return {"status": "error", "error": str(e)}


# Global registry instance
registry = MCPToolRegistry()

# Import tool modules to trigger registration
from . import projects  # noqa: F401 — triggers register_tools()


# ── Pydantic request / response models ─────────────────────────────────────

class ExecuteToolRequest(BaseModel):
    tool: str
    parameters: Dict[str, Any] = {}


class ExecuteToolResponse(BaseModel):
    status: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ── MCP Endpoints ──────────────────────────────────────────────────────────

@router.get("/")
async def mcp_root():
    """MCP service root — capability discovery."""
    return {
        "name": "GNS3 MCP Server",
        "version": "1.0.0",
        "capabilities": {"tools": True, "resources": False, "prompts": False},
    }


@router.get("/tools")
async def list_tools():
    """List every registered MCP tool with its parameter schema."""
    tools = registry.list_tools()
    return {"tools": tools, "count": len(tools)}


@router.post("/execute", response_model=ExecuteToolResponse)
async def execute_tool(
    request: ExecuteToolRequest,
    http_request: Request,
    current_user: schemas.User = Depends(get_current_active_user),
):
    """
    Execute an MCP tool by name.
    Authentication is enforced via the existing JWT mechanism.
    The tool handler receives a Gns3Connector pre-configured with the
    current user's JWT token so it calls GNS3's own REST API (not the
    controller internals), keeping the MCP layer fully decoupled.
    """

    # Extract the raw JWT token from the Authorization header
    auth_header = http_request.headers.get("Authorization", "")
    jwt_token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else None

    # Build the local GNS3 API base URL from the server config
    config = Config.instance().settings
    host = config.Server.host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = config.Server.port
    scheme = "https" if config.Server.enable_ssl else "http"
    server_url = f"{scheme}://{host}:{port}"

    result = await registry.execute(
        request.tool,
        request.parameters,
        current_user=current_user,
        jwt_token=jwt_token,
        server_url=server_url,
    )
    return ExecuteToolResponse(**result)
