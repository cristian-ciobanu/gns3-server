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
MCP tool handlers for device configuration via Nornir + Netmiko.

These tools connect to network device consoles via telnet/SSH and execute
configuration or diagnostic commands. Device connection info is automatically
discovered from the project topology using the device's tags for device_type.

Prerequisites:
  - Device must be started (use node_start / node_start_all)
  - Device must have a 'device_type:<type>' tag set in GNS3
    (right-click → Configure → Tags → add 'device_type:cisco_ios_telnet')
  - Device must have a console port assigned
"""

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


# ── Tool handlers ──────────────────────────────────────────────────────────

def device_config_send_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Send configuration commands to network devices via console."""
    project_id = params.get("project_id")
    device_configs = params.get("device_configs")
    if not project_id or not device_configs:
        return [{"error": "project_id and device_configs are required"}]

    from gns3server.agent.gns3_copilot.tools_v2.config_tools_nornir import ExecuteMultipleDeviceConfigCommands

    tool = ExecuteMultipleDeviceConfigCommands()
    input_data = json.dumps({
        "project_id": project_id,
        "device_configs": device_configs,
    })
    return tool._run(
        input_data,
        jwt_token=gns3_ctx["jwt_token"],
        url=gns3_ctx["server_url"],
    )


def device_command_run_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Run read-only diagnostic (show) commands on network devices."""
    project_id = params.get("project_id")
    device_configs = params.get("device_configs")
    if not project_id or not device_configs:
        return [{"error": "project_id and device_configs (list of {device_name, show_commands}) are required"}]

    from gns3server.agent.gns3_copilot.tools_v2.display_tools_nornir import ExecuteMultipleDeviceCommands

    tool = ExecuteMultipleDeviceCommands()
    input_data = json.dumps({
        "project_id": project_id,
        "device_configs": device_configs,
    })
    return tool._run(
        input_data,
        jwt_token=gns3_ctx["jwt_token"],
        url=gns3_ctx["server_url"],
    )


def vpcs_config_set_handler(params: dict[str, Any], gns3_ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Configure VPCS devices (set IP, gateway, etc.)."""
    project_id = params.get("project_id")
    device_configs = params.get("device_configs")
    if not project_id or not device_configs:
        return [{"error": "project_id and device_configs are required"}]

    from gns3server.agent.gns3_copilot.tools_v2.vpcs_tools_netmiko import VPCSCommands

    tool = VPCSCommands()
    input_data = json.dumps({
        "project_id": project_id,
        "device_configs": device_configs,
    })
    return tool._run(
        input_data,
        jwt_token=gns3_ctx["jwt_token"],
        url=gns3_ctx["server_url"],
    )
