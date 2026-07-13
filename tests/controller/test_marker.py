#!/usr/bin/env python
#
# Copyright (C) 2025 GNS3 Technologies Inc.
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
Controller-layer tests for the traffic-insight marker feature:

* UDPLink.start_marker / stop_marker / update_marker — storage, guards,
  inheritance bypass, and partial-update preservation of render hints.
* Project.create/update/delete_marker_definition — fan-out, sync, cleanup.
* Project.apply_defs_to_new_link and the markers aggregation property.
"""

import pytest
from unittest.mock import MagicMock, patch

from tests.utils import AsyncioMagicMock

from gns3server.controller.udp_link import UDPLink
from gns3server.controller.ports.ethernet_port import EthernetPort
from gns3server.controller.node import Node
from gns3server.controller.controller_error import ControllerError, ControllerNotFoundError


def _valid_bpf():
    """Bypass tcpdump-based BPF validation so tests don't depend on tcpdump."""
    return patch(
        "gns3server.controller.udp_link.validate_bpf_syntax",
        return_value={"valid": True, "error": None},
    )


async def _make_link(project):
    """Build a created UDPLink between two VPCS nodes on a mocked compute."""

    compute = MagicMock()
    compute.id = "local"
    compute.host = "example.com"

    node1 = Node(project, compute, "n1", node_type="vpcs")
    node1._ports = [EthernetPort("E0", 0, 0, 0)]
    node2 = Node(project, compute, "n2", node_type="vpcs")
    node2._ports = [EthernetPort("E0", 0, 0, 1)]

    async def subnet(_other):
        return ("192.168.1.1", "192.168.1.2")

    async def udp_cb(path, data={}, **kwargs):
        response = MagicMock()
        response.json = {"udp_port": 1234}
        return response

    compute.get_ip_on_same_subnet.side_effect = subnet
    compute.post.side_effect = udp_cb
    # start_marker / update_marker push via node.put -> compute.put; make it awaitable.
    compute.put = AsyncioMagicMock()
    compute.delete = AsyncioMagicMock()

    link = UDPLink(project)
    await link.add_node(node1, 0, 0)
    await link.add_node(node2, 0, 1)
    # Register with the project so definition fan-out (which iterates _links) reaches it.
    project._links[link.id] = link
    return link


# ---------------------------------------------------------------------------
# UDPLink.start_marker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_marker_stores_entry(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("icmp", "icmp", tag=7, color="#ff5722", highlight_duration=800)

    entry = link.markers["icmp"]
    assert entry["bpf"] == "icmp"
    assert entry["tag"] == 7
    assert entry["color"] == "#ff5722"
    assert entry["highlight_duration"] == 800
    assert entry["enabled"] is True
    assert entry["capture_node_id"] in {n["node"].id for n in link._nodes}
    assert "inherited_from" not in entry


@pytest.mark.asyncio
async def test_start_marker_rejects_duplicate(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("icmp", "icmp")
        with pytest.raises(ControllerError):
            await link.start_marker("icmp", "tcp")


@pytest.mark.asyncio
async def test_start_marker_rejects_invalid_bpf(project):

    link = await _make_link(project)
    with patch("gns3server.controller.udp_link.validate_bpf_syntax",
               return_value={"valid": False, "error": "bad expression"}):
        with pytest.raises(ControllerError):
            await link.start_marker("bad", "not a real bpf")


# ---------------------------------------------------------------------------
# UDPLink.stop_marker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_marker_removes(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("icmp", "icmp")
        assert "icmp" in link.markers
        await link.stop_marker("icmp")
    assert "icmp" not in link.markers


@pytest.mark.asyncio
async def test_stop_marker_rejects_inherited(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.inherit_marker("arp", {"bpf": "arp"})
        # Per-link delete of an inherited marker must be refused (use the def API).
        with pytest.raises(ControllerError):
            await link.stop_marker("global-arp")
    assert "global-arp" in link.markers  # still present


@pytest.mark.asyncio
async def test_stop_marker_inherited_bypass(project):
    """The def-delete path passes inherited=True to remove inherited copies."""

    with _valid_bpf():
        link = await _make_link(project)
        await link.inherit_marker("arp", {"bpf": "arp"})
        await link.stop_marker("global-arp", inherited=True)
    assert "global-arp" not in link.markers


@pytest.mark.asyncio
async def test_stop_marker_unknown_raises(project):

    link = await _make_link(project)
    with pytest.raises(ControllerNotFoundError):
        await link.stop_marker("nope")


# ---------------------------------------------------------------------------
# UDPLink.update_marker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_marker_preserves_render_hints(project):
    """A partial update (bpf only) must not reset color/highlight_duration/tag."""

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("m", "icmp", tag=1, color="#ff5722", highlight_duration=800)
        await link.update_marker("m", bpf="tcp port 80")

    entry = link.markers["m"]
    assert entry["bpf"] == "tcp port 80"
    assert entry["color"] == "#ff5722"          # preserved
    assert entry["highlight_duration"] == 800   # preserved
    assert entry["tag"] == 1                    # preserved


@pytest.mark.asyncio
async def test_update_marker_changes_fields(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("m", "icmp", highlight_duration=800)
        await link.update_marker("m", highlight_duration=1500, enabled=False, tag=9)

    entry = link.markers["m"]
    assert entry["highlight_duration"] == 1500
    assert entry["enabled"] is False
    assert entry["tag"] == 9


@pytest.mark.asyncio
async def test_update_marker_rejects_inherited(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.inherit_marker("arp", {"bpf": "arp"})
        with pytest.raises(ControllerError):
            await link.update_marker("global-arp", bpf="tcp")


@pytest.mark.asyncio
async def test_update_marker_inherited_bypass(project):
    """The def-sync path passes inherited=True to update inherited copies."""

    with _valid_bpf():
        link = await _make_link(project)
        await link.inherit_marker("arp", {"bpf": "arp", "highlight_duration": 500})
        await link.update_marker("global-arp", highlight_duration=1200, inherited=True)
    assert link.markers["global-arp"]["highlight_duration"] == 1200


# ---------------------------------------------------------------------------
# Link.inherit_marker + persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inherit_marker_creates_global_copy(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.inherit_marker("arp", {"bpf": "arp", "tag": 3, "color": "#111", "highlight_duration": 400})

    entry = link.markers["global-arp"]
    assert entry["bpf"] == "arp"
    assert entry["tag"] == 3
    assert entry["color"] == "#111"
    assert entry["highlight_duration"] == 400
    assert entry["inherited_from"] == "arp"


@pytest.mark.asyncio
async def test_persist_markers_excludes_inherited(project):
    """Inherited markers are re-created from definitions on load, never persisted."""

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("private", "icmp", highlight_duration=800)
        await link.inherit_marker("arp", {"bpf": "arp"})

    persisted = link._persist_markers()
    assert set(persisted.keys()) == {"private"}
    assert "global-arp" not in persisted


@pytest.mark.asyncio
async def test_asdict_markers_runtime_vs_dump(project):
    """Runtime asdict exposes all markers; topology dump drops inherited ones."""

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("private", "icmp")
        await link.inherit_marker("arp", {"bpf": "arp"})

    runtime = link.asdict()
    assert set(runtime["markers"].keys()) == {"private", "global-arp"}
    dumped = link.asdict(topology_dump=True)
    assert set(dumped["markers"].keys()) == {"private"}


# ---------------------------------------------------------------------------
# Project-level marker definitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_marker_definition_fans_out(project):

    with _valid_bpf():
        link1 = await _make_link(project)
        link2 = await _make_link(project)
        await project.create_marker_definition("arp", "arp", tag=5, highlight_duration=1200)

    for link in (link1, link2):
        entry = link.markers["global-arp"]
        assert entry["inherited_from"] == "arp"
        assert entry["bpf"] == "arp"
        assert entry["highlight_duration"] == 1200
    assert project.marker_definitions["arp"]["highlight_duration"] == 1200


@pytest.mark.asyncio
async def test_update_marker_definition_syncs(project):

    with _valid_bpf():
        link1 = await _make_link(project)
        link2 = await _make_link(project)
        await project.create_marker_definition("arp", "arp", highlight_duration=500)
        await project.update_marker_definition("arp", highlight_duration=1500, bpf="arp or rarp")

    for link in (link1, link2):
        assert link.markers["global-arp"]["highlight_duration"] == 1500
        assert link.markers["global-arp"]["bpf"] == "arp or rarp"
    assert project.marker_definitions["arp"]["highlight_duration"] == 1500


@pytest.mark.asyncio
async def test_delete_marker_definition_clears(project):
    """Regression: deleting a def must remove inherited copies from every link."""

    with _valid_bpf():
        link1 = await _make_link(project)
        link2 = await _make_link(project)
        await project.create_marker_definition("arp", "arp")
        assert "global-arp" in link1.markers
        await project.delete_marker_definition("arp")

    assert "global-arp" not in link1.markers
    assert "global-arp" not in link2.markers
    assert "arp" not in project.marker_definitions


@pytest.mark.asyncio
async def test_apply_defs_to_new_link(project):
    """A link created after a definition exists inherits it automatically."""

    with _valid_bpf():
        await project.create_marker_definition("arp", "arp")
        new_link = await _make_link(project)

    assert "global-arp" in new_link.markers
    assert new_link.markers["global-arp"]["inherited_from"] == "arp"


@pytest.mark.asyncio
async def test_markers_aggregation(project):

    with _valid_bpf():
        link = await _make_link(project)
        await link.start_marker("icmp", "icmp", highlight_duration=800)

    agg = project.markers
    key = f"{link.id}/icmp"
    assert key in agg
    assert agg[key]["bpf"] == "icmp"
    assert agg[key]["highlight_duration"] == 800
    assert agg[key]["link_id"] == link.id
    assert agg[key]["node_id"] == agg[key]["capture_node_id"]
