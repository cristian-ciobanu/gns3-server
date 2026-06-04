# MCP (Model Context Protocol) Service

## Overview

GNS3 Server provides a standard [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) interface, allowing AI assistants like Claude to interact with GNS3 network simulations through SSE (Server-Sent Events) transport.

The MCP service exposes GNS3 project management operations as MCP tools that can be discovered and called by MCP clients.

## Endpoints

| Path | Method | Description |
|------|--------|-------------|
| `/v3/mcp/` | GET | MCP service metadata |
| `/v3/mcp/transport/sse` | GET | SSE stream (MCP connection) |
| `/v3/mcp/transport/messages/` | POST | JSON-RPC messages |

## Authentication

The SSE endpoint requires a valid GNS3 JWT token. It supports two ways to pass the token:

1. **Authorization header** (recommended for Claude Code):
   ```
   Authorization: Bearer <jwt>
   ```

2. **Query parameter** (required for Claude Desktop, since EventSource does not support custom headers):
   ```
   GET /v3/mcp/transport/sse?token=<jwt>
   ```

### Getting a Token

```bash
curl -X POST http://localhost:3080/v3/access/users/authenticate \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'
```

### Token Expiry

Default JWT token lifetime is **1440 minutes (24 hours)**. This can be configured in `gns3_server.conf`:

```ini
jwt_access_token_expire_minutes = 1440  ; 24 hours
```

## Available Tools

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `list_projects` | List all projects | none |
| `get_project` | Get project details | `project_id` |
| `create_project` | Create a project | `name` |
| `delete_project` | Delete a project | `project_id` |
| `open_project` | Open a project | `project_id` |
| `close_project` | Close a project | `project_id` |
| `get_project_stats` | Get project statistics | `project_id` |

## Configuration

### Claude Code (CLI)

```bash
# Get a JWT token
TOKEN=$(curl -s -X POST http://localhost:3080/v3/access/users/authenticate \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Add MCP server
claude mcp add --transport sse My_GNS3_Server \
  http://localhost:3080/v3/mcp/transport/sse \
  -H "Authorization: Bearer $TOKEN"
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "My_GNS3_Server": {
      "url": "http://localhost:3080/v3/mcp/transport/sse?token=your_jwt_token"
    }
  }
}
```

## Architecture

```mermaid
sequenceDiagram
    participant Client as Claude Code / Claude Desktop
    participant MCP as MCP Service
    participant Auth as JWT Auth
    participant GNS3 as GNS3 REST API

    Note over Client: 1. Connect with JWT
    Client->>MCP: GET /sse (token in header or query)
    MCP->>Auth: Validate Token
    Auth-->>MCP: Token Valid
    MCP-->>Client: event: endpoint /messages/?session_id=xxx

    Note over Client: 2. Initialize
    Client->>MCP: POST /messages/ (initialize)
    MCP-->>Client: event: message (protocolVersion, capabilities)

    Note over Client: 3. List & Call Tools
    Client->>MCP: POST /messages/ (tools/list)
    MCP-->>Client: event: message (tools list)

    Client->>MCP: POST /messages/ (tools/call list_projects)
    MCP->>GNS3: Gns3Connector HTTP request
    GNS3-->>MCP: Projects data
    MCP-->>Client: event: message (tool result)
```

## Internal Implementation

- **FastMCP** (Anthropic MCP SDK) is used for tool registration and SSE transport
- The SSE app is mounted as a Starlette sub-application under `/v3/mcp/transport`
- JWT tokens are validated using GNS3's existing `auth_service`
- Tool handlers use `Gns3Connector` (from `custom_gns3fy`) to call GNS3's own REST API, keeping the MCP layer decoupled
- The JWT token is stored in a `contextvars.ContextVar` so it is available within tool handler threads (Python ≥ 3.9 propagates contextvars through `asyncio.to_thread`)

### Source Files

- `gns3server/api/routes/mcp/__init__.py` — FastMCP server, tool definitions, SSE transport, JWT auth wrapper
- `gns3server/api/routes/mcp/projects.py` — Project tool handlers using Gns3Connector
- `gns3server/api/server.py` — Mounts MCP routes via `register_starlette_routes()`
