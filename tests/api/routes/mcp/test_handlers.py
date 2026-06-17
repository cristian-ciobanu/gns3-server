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


@pytest.fixture
def ctx():
    return {"server_url": "http://192.168.1.3:3080", "jwt_token": "token", "jwt_username": "admin"}


class TestGetNodes:

    def test_fields_filter(self, ctx):
        from gns3server.api.routes.mcp.nodes import get_nodes_handler
        with patch("gns3server.api.routes.mcp.nodes._get_connector") as m:
            conn = _mock_conn([
                {"node_id": "n1", "name": "R1", "status": "started", "node_type": "qemu", "console": 5000},
            ])
            m.return_value = conn
            result = get_nodes_handler({"project_id": "p1", "fields": ["name", "status"]}, ctx)
            assert result == {"nodes": [{"name": "R1", "status": "started"}], "count": 1}

    def test_invalid_fields(self, ctx):
        from gns3server.api.routes.mcp.nodes import get_nodes_handler
        with patch("gns3server.api.routes.mcp.nodes._get_connector") as m:
            conn = _mock_conn([])
            m.return_value = conn
            result = get_nodes_handler({"project_id": "p1", "fields": "not-a-list"}, ctx)
            assert "error" in result


class TestCreateNode:

    def test_single_passes_name(self, ctx):
        from gns3server.api.routes.mcp.nodes import create_node_handler
        with patch("gns3server.api.routes.mcp.nodes._get_connector") as m:
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

    def test_fields_filter(self, ctx):
        from gns3server.api.routes.mcp.nodes import create_node_handler
        with patch("gns3server.api.routes.mcp.nodes._get_connector") as m:
            conn = _mock_conn({"node_id": "n1", "name": "R1", "status": "started", "node_type": "qemu", "console": 5000})
            m.return_value = conn
            result = create_node_handler({
                "project_id": "p1", "template_id": "t1",
                "fields": ["node_id", "name"],
            }, ctx)
            assert result == {"node_id": "n1", "name": "R1"}

    def test_fields_validation(self, ctx):
        from gns3server.api.routes.mcp.nodes import create_node_handler
        with patch("gns3server.api.routes.mcp.nodes._get_connector") as m:
            conn = _mock_conn()
            m.return_value = conn
            result = create_node_handler({
                "project_id": "p1", "template_id": "t1", "fields": "not-a-list",
            }, ctx)
            assert "error" in result
            assert "fields must be a list" in result["error"]
            conn.http_call.assert_not_called()

    def test_batch_inherits_template_id(self, ctx):
        from gns3server.api.routes.mcp.nodes import create_node_handler
        with patch("gns3server.api.routes.mcp.nodes._get_connector") as m:
            conn = _mock_conn({"node_id": "n1", "name": "R1"})
            m.return_value = conn
            result = create_node_handler({
                "project_id": "p1", "template_id": "default-tpl",
                "nodes": [{"name": "R1", "x": 0, "y": 0}],
            }, ctx)
            assert result[0]["status"] == "success"

    def test_missing_project_id(self, ctx):
        from gns3server.api.routes.mcp.nodes import create_node_handler
        assert create_node_handler({}, ctx) == {"error": "project_id is required"}


class TestCreateLink:

    def test_compact_format(self, ctx):
        from gns3server.api.routes.mcp.links import create_link_handler
        with patch("gns3server.api.routes.mcp.links._get_connector") as m:
            conn = _mock_conn({"link_id": "l1", "link_type": "ethernet", "nodes": []})
            m.return_value = conn
            result = create_link_handler({
                "project_id": "p1",
                "nodes": ["n1", 0, 0, "n2", 0, 0],
            }, ctx)
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/projects/p1/links",
                json_data={
                    "nodes": [
                        {"node_id": "n1", "adapter_number": 0, "port_number": 0},
                        {"node_id": "n2", "adapter_number": 0, "port_number": 0},
                    ]
                },
            )

    def test_standard_format(self, ctx):
        from gns3server.api.routes.mcp.links import create_link_handler
        with patch("gns3server.api.routes.mcp.links._get_connector") as m:
            conn = _mock_conn({"link_id": "l1"})
            m.return_value = conn
            result = create_link_handler({
                "project_id": "p1",
                "nodes": [
                    {"node_id": "n1", "adapter_number": 0, "port_number": 0},
                    {"node_id": "n2", "adapter_number": 0, "port_number": 0},
                ],
            }, ctx)
            assert result["link_id"] == "l1"

    def test_fields_validation(self, ctx):
        from gns3server.api.routes.mcp.links import create_link_handler
        with patch("gns3server.api.routes.mcp.links._get_connector") as m:
            conn = _mock_conn()
            m.return_value = conn
            result = create_link_handler({
                "project_id": "p1", "fields": "bad",
                "nodes": ["n1", 0, 0, "n2", 0, 0],
            }, ctx)
            assert "error" in result
            assert "fields must be a list" in result["error"]
            conn.http_call.assert_not_called()


class TestAppliance:

    def test_get(self, ctx):
        from gns3server.api.routes.mcp.appliances import get_appliance_handler
        with patch("gns3server.api.routes.mcp.appliances._get_connector") as m:
            conn = _mock_conn({"appliance_id": "a1", "name": "Cisco ISE"})
            m.return_value = conn
            result = get_appliance_handler({"appliance_id": "a1"}, ctx)
            assert result["name"] == "Cisco ISE"

    def test_install_with_version(self, ctx):
        from gns3server.api.routes.mcp.appliances import install_appliance_handler
        with patch("gns3server.api.routes.mcp.appliances._get_connector") as m:
            conn = _mock_conn({"status": "installed"})
            m.return_value = conn
            result = install_appliance_handler({
                "appliance_id": "a1", "version": "2.7.0.356",
            }, ctx)
            conn.http_call.assert_called_with(
                "post", "http://192.168.1.3:3080/v3/appliances/a1/install",
                params={"version": "2.7.0.356"},
            )


class TestTemplates:

    def test_list_fields(self, ctx):
        from gns3server.api.routes.mcp.templates import list_templates_handler
        with patch("gns3server.api.routes.mcp.templates._get_connector") as m:
            conn = _mock_conn([
                {"template_id": "t1", "name": "Cisco 7200", "template_type": "dynamips",
                 "category": "router", "default_name_format": "{name}-{0}"},
            ])
            m.return_value = conn
            result = list_templates_handler({"fields": ["template_id", "name"]}, ctx)
            assert result["templates"] == [{"template_id": "t1", "name": "Cisco 7200"}]

    def test_list_invalid_field(self, ctx):
        from gns3server.api.routes.mcp.templates import list_templates_handler
        with patch("gns3server.api.routes.mcp.templates._get_connector") as m:
            conn = _mock_conn()
            m.return_value = conn
            result = list_templates_handler({"fields": ["does_not_exist"]}, ctx)
            assert "error" in result
