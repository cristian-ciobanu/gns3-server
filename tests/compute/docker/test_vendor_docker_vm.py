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
Tests for the VendorDockerVM subclass (vendor NOS containers, e.g. Nokia SR
Linux) and the Docker manager's class-selection factory.

These tests cover:
  * the factory selecting VendorDockerVM iff console_type == "docker_exec";
  * GNS3_* env parsing (SKIP_INIT, INTERFACE_NAMES, CONSOLE_CMD);
  * init.sh prepend being skipped with GNS3_SKIP_INIT;
  * GNS3_INTERFACE_NAMES renaming injected interfaces (move_to_ns target);
  * the hardcoded /etc/network mount being dropped for SKIP_INIT containers;
  * the docker_exec console dispatch in start();
  * the SKIP_INIT volume bridge and container-side _fix_permissions passes.
"""

import uuid
import os

import pytest
import pytest_asyncio

from unittest.mock import patch, MagicMock, call

from tests.utils import asyncio_patch, AsyncioMagicMock

from gns3server.compute.docker import Docker
from gns3server.compute.docker.docker_vm import DockerVM
from gns3server.compute.docker.vendor_docker_vm import VendorDockerVM
from gns3server.compute.docker.docker_error import DockerError, DockerHttp404Error


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _create_response(vm, entrypoint=None, volumes=None):
    """Build the Docker /containers/create response (with image info merged)."""
    return {
        "Id": "e90e34656806",
        "Warnings": [],
        "Config": {
            "Entrypoint": entrypoint,
            "Cmd": [],
            "Volumes": volumes,
        },
    }


@pytest_asyncio.fixture
async def manager(port_manager):

    m = Docker.instance()
    m.port_manager = port_manager
    return m


def _make_vm(compute_project, manager, environment=None, console_type="docker_exec",
             extra_volumes=None, adapters=4):
    """Build a VendorDockerVM with a fake cid (no create() called)."""
    vm = VendorDockerVM(
        "srlinux-1", str(uuid.uuid4()), compute_project, manager, "srlinux:latest",
        console_type=console_type, environment=environment,
        extra_volumes=extra_volumes or [], adapters=adapters,
    )
    vm._cid = "e90e34656842"
    return vm


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------

def test_factory_selects_vendor_when_docker_exec(manager):

    assert manager._select_node_class(console_type="docker_exec") is VendorDockerVM


def test_factory_selects_base_for_other_console_types(manager):

    for ct in ("telnet", "ssh", "vnc", "http", "https", "none", "spice"):
        assert manager._select_node_class(console_type=ct) is DockerVM, ct


def test_factory_default_is_base(manager):

    assert manager._select_node_class() is DockerVM


@pytest.mark.asyncio
async def test_create_node_sets_node_class(manager, compute_project, monkeypatch):
    """create_node() must switch _NODE_CLASS based on console_type."""

    captured = {}

    async def fake_super_create_node(name, project_id, node_id, *args, **kwargs):
        # record which class create_node selected before delegating
        captured["cls"] = manager._NODE_CLASS
        return None

    monkeypatch.setattr(
        "gns3server.compute.base_manager.BaseManager.create_node",
        fake_super_create_node,
    )

    await manager.create_node("v", compute_project.id, str(uuid.uuid4()),
                              "srlinux:latest", console_type="docker_exec")
    assert captured["cls"] is VendorDockerVM

    await manager.create_node("v", compute_project.id, str(uuid.uuid4()),
                              "ubuntu:latest", console_type="telnet")
    assert captured["cls"] is DockerVM


# ---------------------------------------------------------------------------
# GNS3_* env parsing
# ---------------------------------------------------------------------------

def test_env_skip_init_true(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_SKIP_INIT=1")
    assert vm._gns3_init is False


def test_env_skip_init_absent_defaults_true(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="FOO=bar")
    assert vm._gns3_init is True


def test_env_interface_names(compute_project, manager):

    vm = _make_vm(compute_project, manager,
                  environment="GNS3_INTERFACE_NAMES=mgmt0,e1-1,e1-2,e1-3")
    assert vm._interface_names == ["mgmt0", "e1-1", "e1-2", "e1-3"]


def test_env_console_cmd(compute_project, manager):

    vm = _make_vm(compute_project, manager,
                  environment="GNS3_CONSOLE_CMD=/opt/srlinux/bin/sr_cli")
    assert vm._console_cmd == "/opt/srlinux/bin/sr_cli"


def test_env_multiple_lines(compute_project, manager):

    vm = _make_vm(compute_project, manager,
                  environment=("GNS3_SKIP_INIT=1\n"
                               "GNS3_INTERFACE_NAMES=mgmt0,e1-1\n"
                               "GNS3_CONSOLE_CMD=/opt/srlinux/bin/sr_cli\n"))
    assert vm._gns3_init is False
    assert vm._interface_names == ["mgmt0", "e1-1"]
    assert vm._console_cmd == "/opt/srlinux/bin/sr_cli"


def test_env_console_cmd_default_none(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    assert vm._console_cmd is None


# ---------------------------------------------------------------------------
# create() — init.sh skip, GNS3_MAX_ETHERNET, /etc/network drop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_skip_init_omits_init_sh(compute_project, manager):

    response = _create_response(None, entrypoint=["/init"])
    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "srlinux"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=response) as mock:
            vm = VendorDockerVM("srlinux-1", str(uuid.uuid4()), compute_project,
                                manager, "srlinux:latest",
                                console_type="docker_exec",
                                environment="GNS3_SKIP_INIT=1")
            await vm.create()
            # the Entrypoint must NOT contain /gns3/init.sh
            sent = mock.call_args.kwargs["data"]
            assert "/gns3/init.sh" not in sent["Entrypoint"]
            assert sent["Entrypoint"] == ["/init"]


@pytest.mark.asyncio
async def test_create_without_skip_init_prepends_init_sh(compute_project, manager):

    response = _create_response(None, entrypoint=["/init"])
    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "srlinux"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=response) as mock:
            vm = VendorDockerVM("srlinux-1", str(uuid.uuid4()), compute_project,
                                manager, "srlinux:latest",
                                console_type="docker_exec")
            await vm.create()
            sent = mock.call_args.kwargs["data"]
            # init.sh IS prepended when not skipping
            assert sent["Entrypoint"][0] == "/gns3/init.sh"


@pytest.mark.asyncio
async def test_create_interface_names_sets_max_ethernet(compute_project, manager):

    response = _create_response(None)
    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "srlinux"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=response) as mock:
            vm = VendorDockerVM("srlinux-1", str(uuid.uuid4()), compute_project,
                                manager, "srlinux:latest", adapters=4,
                                console_type="docker_exec",
                                environment="GNS3_SKIP_INIT=1\nGNS3_INTERFACE_NAMES=mgmt0,e1-1,e1-2,e1-3")
            await vm.create()
            sent = mock.call_args.kwargs["data"]
            # last interface (adapter index 3) should be e1-3, not eth3
            assert any(v == "GNS3_MAX_ETHERNET=e1-3" for v in sent["Env"])


@pytest.mark.asyncio
async def test_create_drops_etc_network_for_skip_init(compute_project, manager):

    response = _create_response(None, volumes={"/opt/srlinux/appmgr": None})
    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "srlinux"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=response) as mock:
            vm = VendorDockerVM("srlinux-1", str(uuid.uuid4()), compute_project,
                                manager, "srlinux:latest",
                                console_type="docker_exec",
                                environment="GNS3_SKIP_INIT=1",
                                extra_volumes=["/etc/opt/srlinux"])
            await vm.create()
            sent = mock.call_args.kwargs["data"]
            targets = [m["Target"] for m in sent["HostConfig"]["Mounts"]]
            # /etc/network must NOT be mounted
            assert "/gns3volumes/etc/network" not in targets
            # but the declared volumes ARE mounted
            assert "/gns3volumes/opt/srlinux/appmgr" in targets
            assert "/gns3volumes/etc/opt/srlinux" in targets
            # GNS3_VOLUMES env must also exclude /etc/network
            vol_env = [v for v in sent["Env"] if v.startswith("GNS3_VOLUMES=")][0]
            assert "/etc/network" not in vol_env
            # host skeleton dir removed
            assert not os.path.exists(os.path.join(vm.working_dir, "etc", "network"))


@pytest.mark.asyncio
async def test_create_keeps_etc_network_without_skip_init(compute_project, manager):

    response = _create_response(None)
    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "srlinux"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=response) as mock:
            vm = VendorDockerVM("srlinux-1", str(uuid.uuid4()), compute_project,
                                manager, "srlinux:latest",
                                console_type="docker_exec")
            await vm.create()
            sent = mock.call_args.kwargs["data"]
            targets = [m["Target"] for m in sent["HostConfig"]["Mounts"]]
            assert "/gns3volumes/etc/network" in targets


# ---------------------------------------------------------------------------
# Interface renaming (_get_container_ifname / move_to_ns)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_move_to_ns_uses_renamed_interface(compute_project, manager):

    vm = _make_vm(compute_project, manager,
                  environment="GNS3_SKIP_INIT=1\nGNS3_INTERFACE_NAMES=mgmt0,e1-1,e1-2,e1-3")
    vm._ubridge_hypervisor = MagicMock()
    vm._namespace = 42
    nio = manager.create_nio({"type": "nio_udp", "lport": 4242, "rport": 4343, "rhost": "127.0.0.1"})
    await vm._add_ubridge_connection(nio, 0)
    # adapter 0 should be renamed to mgmt0
    move_calls = [c for c in vm._ubridge_hypervisor.method_calls if "move_to_ns" in str(c)]
    assert move_calls, "move_to_ns was not sent"
    assert call.send("docker move_to_ns tap-gns3-e0 42 mgmt0") in move_calls


@pytest.mark.asyncio
async def test_move_to_ns_falls_back_to_eth(compute_project, manager):

    vm = _make_vm(compute_project, manager)  # no INTERFACE_NAMES
    vm._ubridge_hypervisor = MagicMock()
    vm._namespace = 42
    nio = manager.create_nio({"type": "nio_udp", "lport": 4242, "rport": 4343, "rhost": "127.0.0.1"})
    await vm._add_ubridge_connection(nio, 1)
    move_calls = [c for c in vm._ubridge_hypervisor.method_calls if "move_to_ns" in str(c)]
    assert call.send("docker move_to_ns tap-gns3-e0 42 eth1") in move_calls


# ---------------------------------------------------------------------------
# start() — docker_exec console dispatch + volume bridge + permission fix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_docker_exec_dispatches_console(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_SKIP_INIT=1")
    vm.adapters = 1
    vm._get_container_state = AsyncioMagicMock(return_value="stopped")
    vm._start_ubridge = AsyncioMagicMock()
    vm._get_namespace = AsyncioMagicMock(return_value=42)
    vm._add_ubridge_connection = AsyncioMagicMock()
    vm._start_docker_exec_console = AsyncioMagicMock()
    vm._setup_skip_init_volumes = AsyncioMagicMock()
    vm._fix_permissions = AsyncioMagicMock()

    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    vm._start_docker_exec_console.assert_called_once()
    assert vm.status == "started"
    # SKIP_INIT path runs the volume bridge + permission fix
    vm._setup_skip_init_volumes.assert_called_once()
    vm._fix_permissions.assert_called_once()


@pytest.mark.asyncio
async def test_start_without_skip_init_skips_vendor_passes(compute_project, manager):

    vm = _make_vm(compute_project, manager)  # no SKIP_INIT
    vm.adapters = 1
    vm.console_type = "docker_exec"
    vm._get_container_state = AsyncioMagicMock(return_value="stopped")
    vm._start_ubridge = AsyncioMagicMock()
    vm._get_namespace = AsyncioMagicMock(return_value=42)
    vm._add_ubridge_connection = AsyncioMagicMock()
    vm._start_docker_exec_console = AsyncioMagicMock()
    vm._setup_skip_init_volumes = AsyncioMagicMock()
    vm._fix_permissions = AsyncioMagicMock()

    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    # init.sh runs (no SKIP_INIT) → no vendor bridge/fix passes
    vm._setup_skip_init_volumes.assert_not_called()


# ---------------------------------------------------------------------------
# _fix_permissions — container-side, skips dead containers, targets /gns3volumes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fix_permissions_skips_dead_container(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_SKIP_INIT=1")
    vm._volumes = ["/etc/opt/srlinux"]
    vm._get_container_state = AsyncioMagicMock(return_value="exited")

    with patch("asyncio.subprocess.create_subprocess_exec") as mock_exec:
        await vm._fix_permissions()
        # must NOT exec into a dead container
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_fix_permissions_skips_missing_container(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_SKIP_INIT=1")
    vm._volumes = ["/etc/opt/srlinux"]
    vm._get_container_state = AsyncioMagicMock(side_effect=DockerHttp404Error("nope"))

    with patch("asyncio.subprocess.create_subprocess_exec") as mock_exec:
        await vm._fix_permissions()
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_fix_permissions_targets_gns3volumes(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_SKIP_INIT=1")
    vm._volumes = ["/etc/opt/srlinux", "/var/log/srlinux"]
    vm._get_container_state = AsyncioMagicMock(return_value="running")

    proc = MagicMock()
    proc.wait = AsyncioMagicMock(return_value=0)
    proc.returncode = 0
    proc.stderr = MagicMock()
    proc.stderr.read = AsyncioMagicMock(return_value=b"")

    with patch("asyncio.subprocess.create_subprocess_exec",
               return_value=proc) as mock_exec:
        await vm._fix_permissions()
        # one exec per volume
        assert mock_exec.call_count == 2
        # each script must target /gns3volumes<volume>, not the raw path
        for call_obj in mock_exec.call_args_list:
            script = call_obj.args[-1]  # last positional arg is the sh -c script
            assert "/gns3volumes" in script
            # must NOT chown the in-container path directly
            assert 'chown' in script and '"/gns3volumes' in script


# ---------------------------------------------------------------------------
# _setup_skip_init_volumes — bridge via docker exec
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_skip_init_volumes_runs_exec(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_SKIP_INIT=1",
                  extra_volumes=["/etc/opt/srlinux"])
    vm._volumes = ["/etc/opt/srlinux"]

    proc = MagicMock()
    proc.communicate = AsyncioMagicMock(return_value=(b"", b""))
    proc.returncode = 0

    with patch("asyncio.subprocess.create_subprocess_exec",
               return_value=proc) as mock_exec:
        await vm._setup_skip_init_volumes()
        assert mock_exec.call_count == 1
        script = mock_exec.call_args.args[-1]
        # must do the bind mount
        assert "mount --bind" in script
        assert "/gns3volumes/etc/opt/srlinux" in script


# ---------------------------------------------------------------------------
# _cleanup_console_resources
# ---------------------------------------------------------------------------

def test_cleanup_console_resources_closes_writer(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    writer = MagicMock()
    vm._console_exec_writer = writer
    vm._cleanup_console_resources()
    writer.close.assert_called_once()
    assert vm._console_exec_writer is None


def test_cleanup_console_resources_no_writer(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    vm._console_exec_writer = None
    # must not raise
    vm._cleanup_console_resources()
