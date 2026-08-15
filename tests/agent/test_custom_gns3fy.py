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
