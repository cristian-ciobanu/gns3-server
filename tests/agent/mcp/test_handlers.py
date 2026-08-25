"""
MCP handler unit tests with mocked Gns3Connector.

Tests that handlers correctly transform tool parameters into HTTP calls.
"""
import pytest
from unittest.mock import MagicMock, patch


def _mock_conn(json_result=None):
    """Create a mocked Gns3Connector with base_url and http_call."""
    conn = MagicMock()
    conn.base_url = "http://192.168.1.3:3080/v3"
    conn.http_call.return_value.json.return_value = json_result or {"status": "ok"}
    return conn


BASE = "gns3server.agent.mcp"
AH = "gns3server.agent.gns3_copilot.gns3_client.api_handlers"  # node/link handlers sunk here


@pytest.fixture
def ctx():
    return {"server_url": "http://192.168.1.3:3080", "jwt_token": "token", "jwt_username": "admin"}


# ── Project ─────────────────────────────────────────────────────────────


class TestProject:

    mod = "projects"

    def test_list(self, ctx):
        from gns3server.agent.mcp.projects import list_projects_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn([{"project_id": "p1", "name": "Test", "status": "opened"}])
            result = list_projects_handler({}, ctx)
            assert result["count"] == 1

    def test_get(self, ctx):
        from gns3server.agent.mcp.projects import get_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"project_id": "p1"})
            result = get_project_handler({"project_id": "p1"}, ctx)
            assert result["project_id"] == "p1"

    def test_get_missing_id(self, ctx):
        from gns3server.agent.mcp.projects import get_project_handler
        assert "error" in get_project_handler({}, ctx)

    def test_create(self, ctx):
        from gns3server.agent.mcp.projects import create_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            conn = _mock_conn({"project_id": "p1"})
            m.return_value = conn
            result = create_project_handler({"name": "New", "auto_close": False}, ctx)
            assert result["project_id"] == "p1"
            conn.http_call.assert_called_once_with(
                "post", f"{conn.base_url}/projects", json_data={"name": "New", "auto_close": False}
            )

    def test_create_without_auto_close(self, ctx):
        from gns3server.agent.mcp.projects import create_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            conn = _mock_conn({"project_id": "p2"})
            m.return_value = conn
            create_project_handler({"name": "New"}, ctx)
            conn.http_call.assert_called_once_with(
                "post", f"{conn.base_url}/projects", json_data={"name": "New"}
            )

    def test_delete(self, ctx):
        from gns3server.agent.mcp.projects import delete_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({})
            result = delete_project_handler({"project_id": "p1"}, ctx)
            assert "message" in result

    def test_open(self, ctx):
        from gns3server.agent.mcp.projects import open_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"status": "opened"})
            result = open_project_handler({"project_id": "p1"}, ctx)
            assert result["status"] == "opened"

    def test_close(self, ctx):
        from gns3server.agent.mcp.projects import close_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"status": "closed"})
            result = close_project_handler({"project_id": "p1"}, ctx)
            assert "error" not in result

    def test_update(self, ctx):
        from gns3server.agent.mcp.projects import update_project_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"name": "Updated"})
            result = update_project_handler({"project_id": "p1", "name": "Updated"}, ctx)
            assert result["name"] == "Updated"

    def test_stats(self, ctx):
        from gns3server.agent.mcp.projects import get_project_stats_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"nodes": 5, "links": 3})
            result = get_project_stats_handler({"project_id": "p1"}, ctx)
            assert result["nodes"] == 5


# ── Node ────────────────────────────────────────────────────────────────


class TestNode:


    def test_list_fields(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import get_nodes_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn([
                {"node_id": "n1", "name": "R1", "status": "started", "node_type": "qemu", "console": 5000},
            ])
            result = get_nodes_handler({"project_id": "p1", "fields": ["name", "status"]}, ctx)
            assert result == {"nodes": [{"name": "R1", "status": "started"}], "count": 1}

    def test_list_invalid_fields(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import get_nodes_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn([])
            result = get_nodes_handler({"project_id": "p1", "fields": "not-a-list"}, ctx)
            assert "error" in result

    def test_get(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import get_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"node_id": "n1", "name": "R1"})
            result = get_node_handler({"project_id": "p1", "node_id": "n1"}, ctx)
            assert result["name"] == "R1"

    def test_create_single_passes_name(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_node_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"node_id": "n1", "name": "MyRouter"})
            m.return_value = conn
            result = create_node_handler({
                "project_id": "p1", "template_id": "t1",
                "name": "MyRouter", "x": 100, "y": 200,
            }, ctx)
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p1/templates/t1",
                json_data={"x": 100, "y": 200, "compute_id": "local", "name": "MyRouter"},
            )
            assert result == {"node_id": "n1", "name": "MyRouter"}

    def test_create_fields_filter(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"node_id": "n1", "name": "R1", "status": "started"})
            result = create_node_handler({
                "project_id": "p1", "template_id": "t1",
                "fields": ["node_id", "name"],
            }, ctx)
            assert result == {"node_id": "n1", "name": "R1"}

    def test_create_fields_validation(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_node_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn()
            m.return_value = conn
            result = create_node_handler({
                "project_id": "p1", "template_id": "t1", "fields": "not-a-list",
            }, ctx)
            assert "error" in result
            assert "fields must be a list" in result["error"]
            conn.http_call.assert_not_called()

    def test_create_batch_inherits_template_id(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"node_id": "n1", "name": "R1"})
            result = create_node_handler({
                "project_id": "p1", "template_id": "default-tpl",
                "nodes": [{"name": "R1", "x": 0, "y": 0}],
            }, ctx)
            assert result[0]["status"] == "success"

    def test_create_missing_project_id(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_node_handler
        assert create_node_handler({}, ctx) == {"error": "project_id is required"}

    def test_delete_batch(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import delete_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({})
            result = delete_node_handler({"project_id": "p1", "node_ids": ["n1", "n2"]}, ctx)
            assert len(result) == 2

    def test_start_batch(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import start_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"status": "started"})
            result = start_node_handler({"project_id": "p1", "node_ids": ["n1"]}, ctx)
            assert result[0]["status"] == "success"

    def test_stop_batch(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import stop_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"status": "stopped"})
            result = stop_node_handler({"project_id": "p1", "node_ids": ["n1"]}, ctx)
            assert result[0]["status"] == "success"

    def test_suspend_batch(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import suspend_node_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"status": "suspended"})
            result = suspend_node_handler({"project_id": "p1", "node_ids": ["n1"]}, ctx)
            assert result[0]["status"] == "success"

    def test_console(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import get_node_console_info_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"console_url": "ws://host/console"})
            result = get_node_console_info_handler({"project_id": "p1", "node_id": "n1"}, ctx)
            assert "command" in result


# ── Link ────────────────────────────────────────────────────────────────


class TestLink:


    def test_list(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import get_links_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn([{"link_id": "l1", "link_type": "ethernet"}])
            result = get_links_handler({"project_id": "p1", "fields": ["link_id"]}, ctx)
            assert result["links"] == [{"link_id": "l1"}]

    def test_get(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import get_link_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"link_id": "l1", "link_type": "ethernet"})
            result = get_link_handler({"project_id": "p1", "link_id": "l1"}, ctx)
            assert result["link_id"] == "l1"

    def test_create_compact_format(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_link_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"link_id": "l1", "link_type": "ethernet", "nodes": []})
            m.return_value = conn
            result = create_link_handler({
                "project_id": "p1",
                "nodes": ["n1", 0, 0, "n2", 0, 0],
            }, ctx)
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p1/links",
                json_data={"nodes": [
                    {"node_id": "n1", "adapter_number": 0, "port_number": 0},
                    {"node_id": "n2", "adapter_number": 0, "port_number": 0},
                ]},
            )

    def test_create_standard_format(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_link_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"link_id": "l1"})
            result = create_link_handler({
                "project_id": "p1",
                "nodes": [
                    {"node_id": "n1", "adapter_number": 0, "port_number": 0},
                    {"node_id": "n2", "adapter_number": 0, "port_number": 0},
                ],
            }, ctx)
            assert result["link_id"] == "l1"

    def test_create_fields_validation(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import create_link_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn()
            m.return_value = conn
            result = create_link_handler({
                "project_id": "p1", "fields": "bad",
                "nodes": ["n1", 0, 0, "n2", 0, 0],
            }, ctx)
            assert "error" in result
            assert "fields must be a list" in result["error"]
            conn.http_call.assert_not_called()

    def test_delete_batch(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import delete_link_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({})
            result = delete_link_handler({"project_id": "p1", "link_ids": ["l1", "l2"]}, ctx)
            assert len(result) == 2

    def test_update(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import update_link_handler
        with patch(f"{AH}._get_connector") as m:
            m.return_value = _mock_conn({"link_id": "l1", "suspend": True})
            result = update_link_handler({
                "project_id": "p1", "link_id": "l1", "suspend": True,
            }, ctx)
            assert result["suspend"] is True


# ── Appliance ───────────────────────────────────────────────────────────


class TestAppliance:

    mod = "appliances"

    def test_get(self, ctx):
        from gns3server.agent.mcp.appliances import get_appliance_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"appliance_id": "a1", "name": "Cisco ISE"})
            result = get_appliance_handler({"appliance_id": "a1"}, ctx)
            assert result["name"] == "Cisco ISE"

    def test_install_with_version(self, ctx):
        from gns3server.agent.mcp.appliances import install_appliance_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            conn = _mock_conn({"status": "installed"})
            m.return_value = conn
            result = install_appliance_handler({
                "appliance_id": "a1", "version": "2.7.0.356",
            }, ctx)
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/appliances/a1/install",
                params={"version": "2.7.0.356"},
            )

    def test_install_missing_id(self, ctx):
        from gns3server.agent.mcp.appliances import install_appliance_handler
        result = install_appliance_handler({}, ctx)
        assert "error" in result


# ── Template ────────────────────────────────────────────────────────────


class TestTemplate:

    mod = "templates"

    def test_list_fields(self, ctx):
        from gns3server.agent.mcp.templates import list_templates_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn([
                {"template_id": "t1", "name": "Cisco 7200", "template_type": "dynamips",
                 "category": "router", "default_name_format": "{name}-{0}"},
            ])
            result = list_templates_handler({"fields": ["template_id", "name"]}, ctx)
            assert result["templates"] == [{"template_id": "t1", "name": "Cisco 7200"}]

    def test_list_invalid_field(self, ctx):
        from gns3server.agent.mcp.templates import list_templates_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn()
            result = list_templates_handler({"fields": ["does_not_exist"]}, ctx)
            assert "error" in result

    def test_get(self, ctx):
        from gns3server.agent.mcp.templates import get_template_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({"template_id": "t1", "name": "Test"})
            result = get_template_handler({"template_id": "t1"}, ctx)
            assert result["name"] == "Test"

    def test_delete(self, ctx):
        from gns3server.agent.mcp.templates import delete_template_handler
        with patch(f"{BASE}.{self.mod}._get_connector") as m:
            m.return_value = _mock_conn({})
            result = delete_template_handler({"template_id": "t1"}, ctx)
            assert "deleted" in str(result).lower()


# ── Marker (traffic-insight) ────────────────────────────────────────────


class TestLinkMarker:
    """link_marker_handler direction tri-state: omit=preserve, tx/rx=set, both=clear (→ null)."""


    def test_update_direction_both_clears(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import link_marker_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "update",
                 "marker_name": "icmp", "direction": "both"}, ctx,
            )
            conn.http_call.assert_called_with(
                "put", "http://192.168.1.3:3080/v3/projects/p/links/l/markers/icmp",
                json_data={"direction": None},
            )

    def test_update_direction_tx_sets(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import link_marker_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "update",
                 "marker_name": "icmp", "direction": "tx"}, ctx,
            )
            conn.http_call.assert_called_with(
                "put", "http://192.168.1.3:3080/v3/projects/p/links/l/markers/icmp",
                json_data={"direction": "tx"},
            )

    def test_update_direction_omitted_preserved(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import link_marker_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "update",
                 "marker_name": "icmp", "tag": 1}, ctx,
            )
            conn.http_call.assert_called_with(
                "put", "http://192.168.1.3:3080/v3/projects/p/links/l/markers/icmp",
                json_data={"tag": 1},
            )

    def test_create_direction_both_omitted(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import link_marker_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "create",
                 "bpf": "icmp", "direction": "both"}, ctx,
            )
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p/links/l/markers",
                json_data={"bpf": "icmp"},
            )

    def test_create_data_link_type_passthrough(self, ctx):
        """create passes a serial WAN encapsulation through; update ignores it."""
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import link_marker_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "create",
                 "bpf": "icmp", "data_link_type": "DLT_C_HDLC"}, ctx,
            )
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p/links/l/markers",
                json_data={"bpf": "icmp", "data_link_type": "DLT_C_HDLC"},
            )

            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "update",
                 "marker_name": "icmp", "tag": 1, "data_link_type": "DLT_PPP_SERIAL"}, ctx,
            )
            # create-only: dropped from the update body
            conn.http_call.assert_called_with(
                "put", "http://192.168.1.3:3080/v3/projects/p/links/l/markers/icmp",
                json_data={"tag": 1},
            )

    def test_create_direction_tx(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import link_marker_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "icmp"})
            m.return_value = conn
            link_marker_handler(
                {"project_id": "p", "link_id": "l", "action": "create",
                 "bpf": "icmp", "direction": "tx"}, ctx,
            )
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p/links/l/markers",
                json_data={"bpf": "icmp", "direction": "tx"},
            )


class TestMarkerDefinition:
    """marker_definition_handler build create/update bodies.

    A definition has NO direction: it fans out to every link and auto-selects its
    capture node on each, so tx/rx (relative to that node) has no consistent
    meaning — any direction passed is ignored, never reaching the request body.
    """


    def test_create_builds_body(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import marker_definition_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "arp"})
            m.return_value = conn
            marker_definition_handler(
                {"project_id": "p", "action": "create",
                 "bpf": "arp", "tag": 1, "color": "#fff"}, ctx,
            )
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p/marker-definitions",
                json_data={"bpf": "arp", "tag": 1, "color": "#fff"},
            )

    def test_create_ignores_direction(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import marker_definition_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "arp"})
            m.return_value = conn
            marker_definition_handler(
                {"project_id": "p", "action": "create",
                 "bpf": "arp", "direction": "tx"}, ctx,
            )
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p/marker-definitions",
                json_data={"bpf": "arp"},
            )

    def test_update_builds_body(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import marker_definition_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "arp"})
            m.return_value = conn
            marker_definition_handler(
                {"project_id": "p", "action": "update",
                 "def_name": "arp", "tag": 1}, ctx,
            )
            conn.http_call.assert_called_with(
                "put", "http://192.168.1.3:3080/v3/projects/p/marker-definitions/arp",
                json_data={"tag": 1},
            )

    def test_update_ignores_direction(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import marker_definition_handler
        with patch(f"{AH}._get_connector") as m:
            conn = _mock_conn({"name": "arp"})
            m.return_value = conn
            marker_definition_handler(
                {"project_id": "p", "action": "update",
                 "def_name": "arp", "tag": 1, "direction": "rx"}, ctx,
            )
            conn.http_call.assert_called_with(
                "put", "http://192.168.1.3:3080/v3/projects/p/marker-definitions/arp",
                json_data={"tag": 1},
            )

    def test_update_requires_a_field(self, ctx):
        from gns3server.agent.gns3_copilot.gns3_client.api_handlers import marker_definition_handler
        with patch(f"{AH}._get_connector"):
            result = marker_definition_handler(
                {"project_id": "p", "action": "update", "def_name": "arp"}, ctx,
            )
        assert "error" in result
