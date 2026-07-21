#!/usr/bin/env python
#
# Copyright (C) 2016 GNS3 Technologies Inc.
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


from .controller_error import ControllerError, ControllerNotFoundError
from .link import Link
from .node_types import BUILTIN_NODE_TYPES
from gns3server.utils.packet_filter_validation import validate_bpf_syntax, FilterValidationError

# Node types without a uBridge bridge — a marker filter has nothing to attach to.
# Node types that can host a marker (have a uBridge bridge to attach the
# `mark` filter to).  Mirrors _get_filter_node in link.py, minus "nat"
# (which has no uBridge).
_MARKER_CAPABLE_TYPES = frozenset({
    "vpcs", "qemu", "docker", "iou", "dynamips", "cloud",
})


class UDPLink(Link):
    def __init__(self, project, link_id=None):
        super().__init__(project, link_id=link_id)
        self._created = False
        self._link_data = []

    @property
    def debug_link_data(self):
        """
        Use for the debug exports
        """
        return self._link_data
    
    def _get_node_filters(self, node1, node2):
        """
        Determine which node gets the active filters applied.

        :returns: Tuple of (node1_filters, node2_filters)
        """
        filter_node = self._get_filter_node()
        return (
            self.get_active_filters() if filter_node == node1 else {},
            self.get_active_filters() if filter_node == node2 else {},
        )

    def _markers_for_node(self, node):
        """
        Marker specs (name -> {bpf, tag, link_id}) for the markers whose capture
        side is ``node`` and that are enabled. Routed by capture_node_id so a
        marker only rides the NIO of the node whose uBridge will host it.
        """
        return {
            name: {"bpf": m["bpf"], "tag": m.get("tag"), "link_id": self._id}
            for name, m in self._markers.items()
            if m.get("enabled", True) and m.get("capture_node_id") == node.id
        }

    def _get_node_markers(self, node1, node2):
        """
        Determine which node gets which markers applied.

        :returns: Tuple of (node1_markers, node2_markers)
        """
        return self._markers_for_node(node1), self._markers_for_node(node2)

    async def create(self):
        """
        Create the link on the nodes
        """

        node1 = self._nodes[0]["node"]
        adapter_number1 = self._nodes[0]["adapter_number"]
        port_number1 = self._nodes[0]["port_number"]
        node2 = self._nodes[1]["node"]
        adapter_number2 = self._nodes[1]["adapter_number"]
        port_number2 = self._nodes[1]["port_number"]

        # Get an IP allowing communication between both host
        try:
            (node1_host, node2_host) = await node1.compute.get_ip_on_same_subnet(node2.compute)
        except ValueError as e:
            raise ControllerError(f"Cannot get an IP address on same subnet: {e}")

        # Reserve a UDP port on both side
        # Try pre-allocated ports first (used during batch project loading)
        port = self._project.pop_preallocated_udp_port(node1.compute.id)
        if port is not None:
            self._node1_port = port
        else:
            response = await node1.compute.post(f"/projects/{self._project.id}/ports/udp")
            self._node1_port = response.json["udp_port"]
        port = self._project.pop_preallocated_udp_port(node2.compute.id)
        if port is not None:
            self._node2_port = port
        else:
            response = await node2.compute.post(f"/projects/{self._project.id}/ports/udp")
            self._node2_port = response.json["udp_port"]

        node1_filters, node2_filters = self._get_node_filters(node1, node2)
        node1_markers, node2_markers = self._get_node_markers(node1, node2)

        # Create the tunnel on both side
        self._link_data.append(
            {
                "lport": self._node1_port,
                "rhost": node2_host,
                "rport": self._node2_port,
                "type": "nio_udp",
                "filters": node1_filters,
                "markers": node1_markers,
                "suspend": self._suspended,
            }
        )
        await node1.post(f"/adapters/{adapter_number1}/ports/{port_number1}/nio", data=self._link_data[0], timeout=120)

        self._link_data.append(
            {
                "lport": self._node2_port,
                "rhost": node1_host,
                "rport": self._node1_port,
                "type": "nio_udp",
                "filters": node2_filters,
                "markers": node2_markers,
                "suspend": self._suspended,
            }
        )
        try:
            await node2.post(
                f"/adapters/{adapter_number2}/ports/{port_number2}/nio", data=self._link_data[1], timeout=120
            )
        except Exception as e:
            # We clean the first NIO
            await node1.delete(f"/adapters/{adapter_number1}/ports/{port_number1}/nio", timeout=120)
            raise e
        self._created = True
        # New links automatically inherit every active project-level marker
        # definition so the user doesn't have to reconfigure.
        await self._project.apply_defs_to_new_link(self)

    async def update(self):
        """
        Update the link on the nodes
        """

        if len(self._link_data) == 0:
            return
        node1 = self._nodes[0]["node"]
        node2 = self._nodes[1]["node"]

        node1_filters, node2_filters = self._get_node_filters(node1, node2)
        node1_markers, node2_markers = self._get_node_markers(node1, node2)

        adapter_number1 = self._nodes[0]["adapter_number"]
        port_number1 = self._nodes[0]["port_number"]
        self._link_data[0]["filters"] = node1_filters
        self._link_data[0]["markers"] = node1_markers
        self._link_data[0]["suspend"] = self._suspended
        if node1.node_type not in ("ethernet_switch", "ethernet_hub"):
            await node1.put(
                f"/adapters/{adapter_number1}/ports/{port_number1}/nio", data=self._link_data[0], timeout=120
            )

        adapter_number2 = self._nodes[1]["adapter_number"]
        port_number2 = self._nodes[1]["port_number"]
        self._link_data[1]["filters"] = node2_filters
        self._link_data[1]["markers"] = node2_markers
        self._link_data[1]["suspend"] = self._suspended
        if node2.node_type not in ("ethernet_switch", "ethernet_hub"):
            await node2.put(
                f"/adapters/{adapter_number2}/ports/{port_number2}/nio", data=self._link_data[1], timeout=221
            )

    async def delete(self):
        """
        Delete the link and free the resources
        """
        if not self._created:
            return
        try:
            node1 = self._nodes[0]["node"]
            adapter_number1 = self._nodes[0]["adapter_number"]
            port_number1 = self._nodes[0]["port_number"]
        except IndexError:
            return
        try:
            await node1.delete(f"/adapters/{adapter_number1}/ports/{port_number1}/nio", timeout=120)
        # If the node is already deleted (user selected multiple element and delete all in the same time)
        except ControllerNotFoundError:
            pass

        try:
            node2 = self._nodes[1]["node"]
            adapter_number2 = self._nodes[1]["adapter_number"]
            port_number2 = self._nodes[1]["port_number"]
        except IndexError:
            return
        try:
            await node2.delete(f"/adapters/{adapter_number2}/ports/{port_number2}/nio", timeout=120)
        # If the node is already deleted (user selected multiple element and delete all in the same time)
        except ControllerNotFoundError:
            pass
        await super().delete()

    async def reset(self):
        """
        Reset the link.
        """

        # recreate the link on the compute
        await self.delete()
        await self.create()

    async def start_capture(self, data_link_type="DLT_EN10MB", capture_file_name=None, wireshark=False, jwt_token=None):
        """
        Start capture on a link
        """
        if not capture_file_name:
            capture_file_name = self.default_capture_file_name()
        self._capture_node = self._choose_capture_side()
        data = {"capture_file_name": capture_file_name, "data_link_type": data_link_type}
        await self._capture_node["node"].post(
            "/adapters/{adapter_number}/ports/{port_number}/capture/start".format(
                adapter_number=self._capture_node["adapter_number"], port_number=self._capture_node["port_number"]
            ),
            data=data,
        )
        await super().start_capture(data_link_type=data_link_type, capture_file_name=capture_file_name, wireshark=wireshark, jwt_token=jwt_token)

    async def stop_capture(self):
        """
        Stop capture on a link
        """
        if self._capture_node:
            await self._capture_node["node"].post(
                "/adapters/{adapter_number}/ports/{port_number}/capture/stop".format(
                    adapter_number=self._capture_node["adapter_number"], port_number=self._capture_node["port_number"]
                )
            )
            self._capture_node = None
        await super().stop_capture()

    def _choose_capture_side(self):
        """
        Run capture on the best candidate.

        The ideal candidate is a node who on controller server and always
        running (capture will not be cut off)

        :returns: Node where the capture should run
        """

        for node in self._nodes:
            if (
                node["node"].compute.id == "local"
                and node["node"].node_type in BUILTIN_NODE_TYPES
                and node["node"].status == "started"
            ):
                return node

        for node in self._nodes:
            if node["node"].node_type in BUILTIN_NODE_TYPES and node["node"].status == "started":
                return node

        for node in self._nodes:
            if node["node"].compute.id == "local" and node["node"].status == "started":
                return node

        for node in self._nodes:
            if node["node"].node_type and node["node"].status == "started":
                return node

        raise ControllerError("Cannot capture because there is no running device on this link")

    def _choose_marker_side(self):
        """
        Pick the node that will host the marker, mirroring ``_get_filter_node``
        in link.py.  Only types with a uBridge bridge (``_MARKER_CAPABLE_TYPES``)
        are eligible.  A running node is preferred, but a stopped one is
        accepted — like packet filters, the marker is stored on the NIO and
        applied when the node starts.
        """

        # Prefer started.
        for node in self._nodes:
            if (
                node["node"].node_type in _MARKER_CAPABLE_TYPES
                and node["node"].status == "started"
            ):
                return node

        # Accept stopped but capable (marker rides NIO, applied at start).
        for node in self._nodes:
            if node["node"].node_type in _MARKER_CAPABLE_TYPES:
                return node

        raise ControllerError(
            "Cannot add marker because no device on this link supports "
            "traffic insight"
        )

    async def node_updated(self, node):
        """
        Called when a node member of the link is updated
        """
        if self._capture_node and node == self._capture_node["node"] and node.status != "started":
            await self.stop_capture()
        # Marker clean-up is *not* done on node stop — markers are a persistent
        # link-scoped feature that recovers via NIO on restart (see
        # _ubridge_apply_markers in add_ubridge_udp_connection).  The user
        # explicitly deletes a marker via the REST API, and a marker is torn
        # down automatically only when its link is deleted.

    async def start_marker(self, name, bpf, tag=None, color=None, highlight_duration=None, inherited_from=None):
        """
        Attach a traffic-insight marker to this link.

        State-only model (mirrors ``update_filters``): record the marker in
        ``_markers`` (with its capture-side node id for NIO routing), then push
        via ``self.update()`` so it rides the NIO and is applied by
        ``_ubridge_apply_markers``. No dedicated uBridge round-trip — exactly
        how packet filters are applied.

        :param name: stable filter name — echoed in MARK signals + pcap identity
        :param bpf: libpcap BPF expression
        :param tag: optional correlation id
        :param color: optional hex color for the Web UI (e.g. '#ff5722'); stored
            with the link and persisted in the topology, never sent to uBridge
        :param highlight_duration: optional UI-only hint (milliseconds) for how
            long a match keeps the marker highlighted; stored, never sent to uBridge
        :param inherited_from: def name when this marker is a project-level
            inheritance copy; set automatically, never exposed to REST callers
        """

        if name in self._markers:
            raise ControllerError(f"Marker '{name}' already exists on link {self._id}")

        result = validate_bpf_syntax(bpf)
        if not result.get("valid"):
            raise ControllerError(f"Invalid BPF expression: {result.get('error', 'unknown error')}")

        marker_side = self._choose_marker_side()
        marker_entry = {
            "bpf": bpf,
            "tag": tag,
            "enabled": True,
            "color": color,
            "highlight_duration": highlight_duration,
            "capture_node_id": marker_side["node"].id,
        }
        if inherited_from:
            marker_entry["inherited_from"] = inherited_from
        self._markers[name] = marker_entry
        if self._created:
            await self.update()
        self._project.emit_notification("link.updated", self.asdict())
        self._project.dump()

    async def stop_marker(self, name, inherited=False):
        """
        Remove a traffic-insight marker from this link.

        Drop it from ``_markers`` and push via ``self.update()``: the NIO
        reset+reapply in ``_ubridge_apply_filters``/``_ubridge_apply_markers``
        drops it from uBridge. Mirrors how deleting a packet filter works.

        :param name: filter name to remove
        :param inherited: set by project-level def-delete to bypass the
            inheritance guard (the project layer is the legitimate remover)
        """

        if name not in self._markers:
            raise ControllerNotFoundError(f"Marker '{name}' not found on link {self._id}")

        if self._markers[name].get("inherited_from") and not inherited:
            raise ControllerError(
                f"Marker '{name}' is inherited from the project-level "
                f"definition '{self._markers[name]['inherited_from']}'. "
                "Delete or update it via the marker-definitions API instead."
            )

        del self._markers[name]
        if self._created:
            await self.update()
        self._project.emit_notification("link.updated", self.asdict())
        self._project.dump()

    async def update_marker(self, name, bpf=None, tag=None, enabled=None, color=None, highlight_duration=None, inherited=False):
        """
        Update an existing marker's BPF/tag/enabled/color. Any change pushes via
        ``self.update()``; uBridge picks up the new params on the next NIO
        reset+reapply (same as packet filters).

        :param name: filter name to update
        :param bpf: new BPF expression (None = keep existing)
        :param tag: new tag id (None = keep existing)
        :param enabled: toggle (None = keep existing)
        :param color: new hex color (None = keep existing)
        :param highlight_duration: new UI highlight duration in ms (None = keep existing)
        :param inherited: set by project-level sync to bypass the inheritance
            guard (the project layer is the legitimate editor)
        """

        marker_info = self._markers.get(name)
        if not marker_info:
            raise ControllerNotFoundError(f"Marker '{name}' not found on link {self._id}")

        if marker_info.get("inherited_from") and not inherited:
            raise ControllerError(
                f"Marker '{name}' is inherited from the project-level "
                f"definition '{marker_info['inherited_from']}'. "
                "Update it via the marker-definitions API instead."
            )

        if bpf is not None and bpf != marker_info["bpf"]:
            result = validate_bpf_syntax(bpf)
            if not result.get("valid"):
                raise ControllerError(f"Invalid BPF expression: {result.get('error', 'unknown error')}")
            marker_info["bpf"] = bpf
        if tag is not None:
            marker_info["tag"] = tag
        if enabled is not None:
            marker_info["enabled"] = enabled
        if color is not None:
            marker_info["color"] = color
        if highlight_duration is not None:
            marker_info["highlight_duration"] = highlight_duration

        if self._created:
            await self.update()
        self._project.emit_notification("link.updated", self.asdict())
        self._project.dump()
