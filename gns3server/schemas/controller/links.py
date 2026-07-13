#
# Copyright (C) 2020 GNS3 Technologies Inc.
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

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Tuple
from enum import Enum
from uuid import UUID, uuid4

from .labels import Label


class LinkNode(BaseModel):
    """
    Link node data.
    """

    node_id: UUID
    adapter_number: int
    port_number: int
    label: Optional[Label] = None


class LinkType(str, Enum):
    """
    Link type.
    """

    ethernet = "ethernet"
    serial = "serial"


class LinkStyle(BaseModel):

    color: Optional[str] = None
    width: Optional[int] = None
    type: Optional[int] = None
    link_type: Optional[str] = None
    bezier_curviness: Optional[int] = None
    flowchart_roundness: Optional[int] = None
    control_offset: Optional[Tuple[float, float]] = None


class LinkBase(BaseModel):
    """
    Link data.
    """

    nodes: Optional[List[LinkNode]] = Field(None, min_length=0, max_length=2)
    suspend: Optional[bool] = None
    link_style: Optional[LinkStyle] = None
    filters: Optional[dict] = None
    markers: Optional[dict] = Field(
        None,
        description="Traffic-insight markers on this link: name → {bpf, tag, enabled}"
    )
    show_filters_icon: Optional[bool] = Field(
        True,
        description="Show filters icon in Web UI"
    )


class LinkCreate(LinkBase):

    link_id: UUID = Field(default_factory=uuid4)
    nodes: List[LinkNode] = Field(..., min_length=2, max_length=2)


class LinkUpdate(LinkBase):

    pass


class Link(LinkBase):

    link_id: UUID
    project_id: Optional[UUID] = None
    link_type: Optional[LinkType] = None
    capturing: Optional[bool] = Field(
        None,
        description="Read only property. True if a capture running on the link"
    )
    capture_file_name: Optional[str] = Field(
        None,
        description="Read only property. The name of the capture file if a capture is running"
    )
    capture_file_path: Optional[str] = Field(
        None,
        description="Read only property. The full path of the capture file if a capture is running"
    )
    capture_compute_id: Optional[str] = Field(
        None,
        description="Read only property. The compute identifier where a capture is running"
    )
    wireshark: Optional[bool] = Field(
        False,
        description="Read only property. True if a Web Wireshark session is active on the link"
    )


class UDPPortInfo(BaseModel):
    """
    UDP port information.
    """

    node_id: UUID
    lport: int
    rhost: str
    rport: int
    type: str

class EthernetPortInfo(BaseModel):
    """
    Ethernet port information.
    """

    node_id: UUID
    interface: str
    type: str


class LinkCapture(BaseModel):
    """
    Link capture data.
    """

    data_link_type: str = "DLT_EN10MB"
    capture_file_name: Optional[str] = None
    wireshark: bool = False


class MarkerCreate(BaseModel):
    """
    Body for attaching a traffic-insight marker to a link.

    ``name`` is optional at the controller REST layer (auto-generated when
    absent) but always set when the controller forwards to the compute.
    """

    name: Optional[str] = Field(
        None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        max_length=128,
        description='Unique marker name on the link. Auto-generated when absent.',
    )
    bpf: str
    tag: Optional[int] = None
    link_id: Optional[str] = None
    color: Optional[str] = Field(
        None,
        description="User-chosen hex color for this marker in the Web UI, e.g. '#ff5722'",
    )
    highlight_duration: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "How long (milliseconds) the Web UI keeps this marker highlighted "
            "after a match. Omitted = use the UI default. Pure render hint — "
            "stored on the link, never sent to uBridge."
        ),
    )
    enabled: Optional[bool] = Field(
        None,
        description="Whether the marker is active. Defaults to true on creation.",
    )

    @field_validator("name")
    @classmethod
    def _check_name_not_reserved(cls, v):
        if v is not None and v.lower().startswith("global"):
            raise ValueError('Names starting with "global" are reserved')
        return v


class MarkerDefinitionCreate(BaseModel):
    """
    Body for creating / updating a project-level marker definition.

    The definition is a template — when applied to a link the marker name is
    prefixed with ``global-`` (e.g. ``arp`` → ``global-arp``) so it can never
    collide with a per-link private marker.
    """

    name: Optional[str] = Field(
        None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        max_length=128,
        description="Unique definition name. Auto-generated when absent.",
    )
    bpf: str
    tag: Optional[int] = None
    color: Optional[str] = Field(
        None,
        description="User-chosen hex color for the marker in the Web UI, e.g. '#ff5722'",
    )
    highlight_duration: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "How long (milliseconds) the Web UI keeps this marker highlighted "
            "after a match. Omitted = use the UI default. Pure render hint — "
            "stored with the definition, never sent to uBridge."
        ),
    )

    @field_validator("name")
    @classmethod
    def _check_name_not_reserved(cls, v):
        if v is not None and v.lower().startswith("global"):
            raise ValueError('Names starting with "global" are reserved')
        return v


