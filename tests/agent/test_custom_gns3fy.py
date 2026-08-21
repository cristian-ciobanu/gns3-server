#!/usr/bin/env python
#
# Copyright (C) 2026 GNS3 Technologies Inc.
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
The vendored gns3fy copy keeps its node/console type lists as literals
(it is shared with the standalone MCP service and cannot import server
enums). These tests fail when the server enums grow a value the vendored
lists have not picked up — exactly what happened with "docker_exec": one
vendor node failing validation made the copilot's topology reader drop
the whole project.
"""

import pytest


def test_console_types_cover_server_enum():
    """
    Every server ConsoleType value must be accepted by the vendored Node model.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import CONSOLE_TYPES
    from gns3server.schemas.common import ConsoleType

    missing = {e.value for e in ConsoleType} - set(CONSOLE_TYPES)
    assert not missing, f"CONSOLE_TYPES drifted from ConsoleType, missing: {missing}"


def test_node_types_cover_server_enum():
    """
    Every server NodeType value must be accepted by the vendored Node model.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import NODE_TYPES
    from gns3server.schemas.controller.nodes import NodeType

    missing = {e.value for e in NodeType} - set(NODE_TYPES)
    assert not missing, f"NODE_TYPES drifted from NodeType, missing: {missing}"


def test_node_accepts_docker_exec_console():
    """
    Vendor NOS nodes use console_type "docker_exec"; the topology reader
    validates the whole node list in one pass, so rejecting it poisoned
    every copilot device tool for the project.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Node

    node = Node(
        name="R1",
        project_id="5f517ce3-1bc6-4245-b866-1a2fbd0ee5a7",
        node_id="0d15c2e6-8f83-4b79-8875-9dbc3e5f2f1e",
        node_type="docker",
        console_type="docker_exec",
        status="started",
    )
    assert node.console_type == "docker_exec"


def test_node_accepts_netmiko_device_type():
    """
    The vendored Node model must keep the netmiko_device_type field so the
    device-port tools can prefer it over the device_type:<type> tag.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Node

    node = Node(
        name="SR1",
        project_id="5f517ce3-1bc6-4245-b866-1a2fbd0ee5a7",
        node_id="0d15c2e6-8f83-4b79-8875-9dbc3e5f2f1e",
        node_type="docker",
        console_type="docker_exec",
        status="started",
        netmiko_device_type="nokia_srl",
    )
    assert node.netmiko_device_type == "nokia_srl"


def test_device_ports_prefer_netmiko_field_over_tag(monkeypatch):
    """
    netmiko_device_type on the node wins over the device_type:<type> tag;
    the tag stays as fallback when the field is missing.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.utils import get_gns3_device_port
    from gns3server.agent.gns3_copilot import gns3_client

    class _FakeTopology:
        def _run(self, project_id=None, jwt_token=None, url=None):
            return {
                "nodes": {
                    "SR1": {
                        "console_port": 5000,
                        "tags": ["device_type:cisco_ios_telnet"],
                        "netmiko_device_type": "nokia_srl",
                    },
                    "R1": {
                        "console_port": 5001,
                        "tags": ["device_type:cisco_ios_telnet", "platform:cisco_ios"],
                        "netmiko_device_type": None,
                    },
                }
            }

    # the function does a lazy from-import inside the body
    monkeypatch.setattr(gns3_client, "GNS3TopologyTool", _FakeTopology)
    hosts = get_gns3_device_port.get_device_ports_from_topology(["SR1", "R1"])

    # field wins over tag
    assert hosts["SR1"]["connection_options"]["netmiko"]["extras"]["device_type"] == "nokia_srl"
    # tag fallback when the field is absent
    assert hosts["R1"]["connection_options"]["netmiko"]["extras"]["device_type"] == "cisco_ios_telnet"
    assert hosts["R1"]["platform"] == "cisco_ios"


def test_device_ports_error_without_any_device_type(monkeypatch):
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.utils import get_gns3_device_port
    from gns3server.agent.gns3_copilot import gns3_client

    class _FakeTopology:
        def _run(self, project_id=None, jwt_token=None, url=None):
            return {
                "nodes": {
                    "R2": {
                        "console_port": 5002,
                        "tags": ["platform:cisco_ios"],
                    },
                }
            }

    # the function does a lazy from-import inside the body
    monkeypatch.setattr(gns3_client, "GNS3TopologyTool", _FakeTopology)
    hosts = get_gns3_device_port.get_device_ports_from_topology(["R2"])

    assert "error" in hosts["R2"]
    assert "netmiko_device_type" in hosts["R2"]["error"]


def test_node_accepts_default_credentials():
    """
    The vendored Node model must keep the default credentials so the
    device-port tools can log into devices that require authentication.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Node

    node = Node(
        name="R1",
        project_id="5f517ce3-1bc6-4245-b866-1a2fbd0ee5a7",
        node_id="0d15c2e6-8f83-4b79-8875-9dbc3e5f2f1e",
        node_type="docker",
        console_type="telnet",
        status="started",
        default_username="admin",
        default_password="admin123",
    )
    assert node.default_username == "admin"
    assert node.default_password == "admin123"


def test_nodes_inventory_emits_default_credentials():
    """
    The inventory dict consumed by get_device_ports_from_topology must
    carry the per-node default credentials.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from types import SimpleNamespace
    from gns3server.agent.gns3_copilot.gns3_client.custom_gns3fy import Node, Project

    project = Project(
        project_id="5f517ce3-1bc6-4245-b866-1a2fbd0ee5a7",
        connector=SimpleNamespace(base_url="http://127.0.0.1:3080"),
    )
    project.nodes = [
        Node(
            name="R1",
            project_id=project.project_id,
            node_id="0d15c2e6-8f83-4b79-8875-9dbc3e5f2f1e",
            node_type="dynamips",
            console=5000,
            default_username="admin",
            default_password="admin123",
        ),
    ]

    inventory = project.nodes_inventory()
    assert inventory["R1"]["default_username"] == "admin"
    assert inventory["R1"]["default_password"] == "admin123"


def test_device_ports_inject_default_credentials(monkeypatch):
    """
    Per-node default credentials become host-level nornir values (which
    override the group's empty fallback); missing or cleared ("") values
    keep inheriting from the group.
    """
    pytest.importorskip("jwt", reason="ai-features extras not installed")
    from gns3server.agent.gns3_copilot.utils import get_gns3_device_port
    from gns3server.agent.gns3_copilot import gns3_client

    class _FakeTopology:
        def _run(self, project_id=None, jwt_token=None, url=None):
            return {
                "nodes": {
                    "R1": {
                        "console_port": 5000,
                        "tags": [],
                        "netmiko_device_type": "cisco_ios_telnet",
                        "default_username": "admin",
                        "default_password": "admin123",
                    },
                    # credentials cleared via PUT arrive as empty strings
                    "R2": {
                        "console_port": 5001,
                        "tags": [],
                        "netmiko_device_type": "cisco_ios_telnet",
                        "default_username": "",
                        "default_password": "",
                    },
                    # never seeded
                    "R3": {
                        "console_port": 5002,
                        "tags": [],
                        "netmiko_device_type": "cisco_ios_telnet",
                    },
                }
            }

    monkeypatch.setattr(gns3_client, "GNS3TopologyTool", _FakeTopology)
    hosts = get_gns3_device_port.get_device_ports_from_topology(["R1", "R2", "R3"])

    # set credentials land at host level
    assert hosts["R1"]["username"] == "admin"
    assert hosts["R1"]["password"] == "admin123"
    # cleared ("") and absent credentials do not override the group fallback
    assert "username" not in hosts["R2"]
    assert "password" not in hosts["R2"]
    assert "username" not in hosts["R3"]
    assert "password" not in hosts["R3"]
