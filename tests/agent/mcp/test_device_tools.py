"""
Device config tool tests with mocked topology and Nornir layers.

Covers the VPCS node-type guard and the error contract shared by
device_config_send / device_show_run / vpcs_config_set.
"""
import json

import pytest
from unittest.mock import MagicMock, patch

VPCS_MOD = "gns3server.agent.gns3_copilot.tools_v2.vpcs_tools_netmiko"


def _topology_ports(node_type):
    """Mocked get_device_ports_from_topology return value for one device."""
    return {"PC1": {"port": 5000, "node_type": node_type}}


class TestVPCSNodeTypeGuard:

    def test_non_vpcs_node_is_rejected(self):
        from gns3server.agent.gns3_copilot.tools_v2.vpcs_tools_netmiko import VPCSCommands

        with patch(f"{VPCS_MOD}.get_device_ports_from_topology",
                   return_value=_topology_ports("iou")) as topo:
            result = VPCSCommands()._run(json.dumps({
                "project_id": "0c0fde25-6ead-4413-a283-ea8fd2324291",
                "device_configs": [{"device_name": "PC1", "commands": ["ip 10.0.0.1/24"]}],
            }))
            assert topo.called
        assert len(result) == 1
        assert result[0]["device_name"] == "PC1"
        assert result[0]["status"] == "failed"
        assert "not a VPCS node" in result[0]["error"]

    def test_missing_node_type_is_rejected(self):
        from gns3server.agent.gns3_copilot.tools_v2.vpcs_tools_netmiko import VPCSCommands

        with patch(f"{VPCS_MOD}.get_device_ports_from_topology",
                   return_value={"PC1": {"port": 5000}}):
            result = VPCSCommands()._run(json.dumps({
                "project_id": "0c0fde25-6ead-4413-a283-ea8fd2324291",
                "device_configs": [{"device_name": "PC1", "commands": ["ip 10.0.0.1/24"]}],
            }))
        assert result[0]["status"] == "failed"
        assert "unknown-type" in result[0]["error"]

    def test_vpcs_node_passes_the_guard(self):
        from gns3server.agent.gns3_copilot.tools_v2.vpcs_tools_netmiko import VPCSCommands

        tool = VPCSCommands()
        nornir = MagicMock()
        host_result = MagicMock(failed=False)
        host_result.result = "OK"
        nornir.run.return_value = {"PC1": host_result}
        with patch(f"{VPCS_MOD}.get_device_ports_from_topology",
                   return_value=_topology_ports("vpcs")), \
             patch.object(VPCSCommands, "_initialize_nornir", return_value=nornir):
            result = tool._run(json.dumps({
                "project_id": "0c0fde25-6ead-4413-a283-ea8fd2324291",
                "device_configs": [{"device_name": "PC1", "commands": ["ip 10.0.0.1/24"]}],
            }))
        assert result[0]["status"] == "success"
        assert result[0]["output"] == "OK"
