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

**30 tools** across 5 categories:

### Project (7)

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `list_projects` | List all projects | none |
| `get_project` | Get project details | `project_id` |
| `create_project` | Create a project | `name` |
| `delete_project` | Delete a project | `project_id` |
| `open_project` | Open a project | `project_id` |
| `close_project` | Close a project | `project_id` |
| `get_project_stats` | Get project statistics | `project_id` |

### Node (10)

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `get_nodes` | List all nodes in a project | `project_id` |
| `get_node` | Get node details | `project_id`, `node_id` |
| `start_node` | Start a node | `project_id`, `node_id` |
| `stop_node` | Stop a node | `project_id`, `node_id` |
| `reload_node` | Reload a node | `project_id`, `node_id` |
| `suspend_node` | Suspend a node | `project_id`, `node_id` |
| `create_node` | Create a node from template | `project_id`, `template_id` |
| `delete_node` | Delete a node | `project_id`, `node_id` |
| `update_node` | Update node properties | `project_id`, `node_id` |
| `get_node_console_info` | Get WebSocket console URL | `project_id`, `node_id` |

### Link (5)

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `get_links` | List all links in a project | `project_id` |
| `get_link` | Get link details | `project_id`, `link_id` |
| `create_link` | Create a link between nodes | `project_id`, `nodes` |
| `delete_link` | Delete a link | `project_id`, `link_id` |
| `update_link` | Update link properties | `project_id`, `link_id` |

### Template (5)

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `list_templates` | List all templates | none |
| `get_template` | Get template details | `template_id` or `name` |
| `create_template` | Create a template | `name`, `template_type` |
| `update_template` | Update a template | `template_id` or `name` |
| `delete_template` | Delete a template | `template_id` or `name` |

### Compute (3)

| Tool | Description | Required Parameters |
|------|-------------|-------------------|
| `list_computes` | List all compute nodes | none |
| `get_compute` | Get compute details | `compute_id` |
| `get_compute_images` | List available images | `emulator` |

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

## Transport Security

MCP server uses FastMCP's DNS rebinding protection to prevent attackers from
exploiting DNS resolution to access the MCP endpoint through unauthorized domains.

### Default Behaviour

DNS rebinding protection is **disabled by default**, allowing connections from
any host. This aligns with GNS3 server's default `host = 0.0.0.0` binding policy,
which is designed for VM distribution scenarios where users access the server
from various network locations.

### Enabling Protection

Add to `gns3_server.conf` under the `[Server]` section:

```ini
; Enable DNS rebinding protection for MCP server
mcp_enable_dns_rebinding_protection = True

; Allowed hosts (comma-separated, "host:*" port wildcard patterns only)
mcp_allowed_hosts = 127.0.0.1:*,localhost:*,192.168.1.3:*

; Allowed origins (comma-separated)
mcp_allowed_origins = http://127.0.0.1:*,http://localhost:*,http://192.168.1.3:*
```

> **Note**: The MCP library only supports `"host:*"` port wildcard patterns
> (e.g., `"192.168.1.3:*"`). Standalone `"*"` wildcards are not supported.

### Protection Mechanism

When protection is enabled, the MCP server validates the `Host` header of
incoming SSE connection requests:

```python
# Verify the request's Host header matches allowed patterns
validate_request → check Host header → 421 Misdirected Request if invalid
```

This prevents DNS rebinding attacks:
1. Attacker registers `evil.com` pointing to your server's IP
2. User's browser makes requests to `evil.com:3080`
3. MCP server checks Host header = `"evil.com:3080"`
4. `"evil.com:3080"` is not in `allowed_hosts` → connection rejected

### Behaviour Summary

| `mcp_enable_dns_rebinding_protection` | Result |
|:---|:---|
| `False` (default) | All hosts allowed |
| `True` + correct hosts configured | Only configured hosts allowed |
| `True` + missing/wrong hosts | Connections rejected with 421 |

For public-facing MCP servers, set `allowed_hosts` to your server's domain name.

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

### Console WebSocket

The `get_node_console_info` tool returns a WebSocket URL for connecting to a node's console. This endpoint is protocol-agnostic — it works for **telnet**, **ssh**, and **vnc** console types alike. The WebSocket simply proxies raw byte streams between the client and the compute node; protocol negotiation (e.g. SSH key exchange) happens on the compute side.

The WebSocket URL is constructed using the server's `_server_url()`, which resolves the host as follows:

| `Server.host` value | Resolved host in URL |
|:---|:---|
| Specific IP or hostname (e.g. `192.168.1.3`) | Used as-is |
| `0.0.0.0` (IPv4 any, default) | Detected via **default route interface IP** |
| `::` (IPv6 any) | Detected via default route interface IP |
| Detection failure | Fallback to `127.0.0.1` |

When `Server.host` is `0.0.0.0` (listen on all interfaces), the MCP server discovers the default route interface IP using a UDP socket connect to `8.8.8.8:80` — no network data is sent, the operating system simply selects the interface that would be used for the default route. This ensures the returned WebSocket URL uses a reachable address (e.g. `192.168.1.3` instead of `127.0.0.1`).

If the configured host is already a specific IP or hostname (not `0.0.0.0`), it is used directly in the URL without modification.

Use `websocat` to connect from the command line:

```bash
# The host in the URL is automatically resolved to a reachable address
websocat ws://192.168.1.3:3080/v3/projects/{project_id}/nodes/{node_id}/console/ws?token=<jwt>
```

### Source Files

| File | Purpose |
|------|---------|
| `gns3server/api/routes/mcp/__init__.py` | FastMCP server, tool decorators, SSE transport, JWT auth wrapper |
| `gns3server/api/routes/mcp/projects.py` | Project tool handlers |
| `gns3server/api/routes/mcp/nodes.py` | Node tool handlers |
| `gns3server/api/routes/mcp/links.py` | Link tool handlers |
| `gns3server/api/routes/mcp/templates.py` | Template tool handlers |
| `gns3server/api/routes/mcp/computes.py` | Compute tool handlers |
| `gns3server/api/server.py` | Mounts MCP routes via `register_starlette_routes()` |
