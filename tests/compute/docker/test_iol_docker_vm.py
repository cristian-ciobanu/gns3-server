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
Tests for the IOLDockerVM subclass (Cisco CML iol-runner images, e.g.
iol-xe/iol-xe:17-18-02) and its unix-socket NIO wiring.

Image-free: everything is asserted against generated files, parsed knobs and
the uBridge command stream.
"""

import asyncio
import glob
import json
import os
import uuid

import pytest
import pytest_asyncio

from unittest.mock import patch, MagicMock, call

from tests.utils import asyncio_patch, AsyncioMagicMock

from gns3server.compute.docker import Docker
from gns3server.compute.docker.docker_vm import DockerVM
from gns3server.compute.docker.vendor_docker_vm import VendorDockerVM
from gns3server.compute.docker.iol_docker_vm import IOLDockerVM
from gns3server.compute.docker.docker_error import DockerError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

IOL_ENTRYPOINT = ["/iol-runner", "-config", "/config/iol-config.json", "-stdio"]


def _create_response(entrypoint=None, volumes=None):
    """Build the Docker /containers/create response (with image info merged)."""
    return {
        "Id": "e90e34656806",
        "Warnings": [],
        "Config": {
            "Entrypoint": entrypoint or IOL_ENTRYPOINT,
            "Cmd": [],
            "Volumes": volumes or {},
        },
    }


@pytest_asyncio.fixture
async def manager(port_manager):

    m = Docker.instance()
    m.port_manager = port_manager
    return m


def _make_vm(compute_project, manager, environment="GNS3_IOL_RUNNER=1",
             extra_volumes=None, adapters=4, console_type="telnet"):
    """Build an IOLDockerVM with a fake cid (no create() called)."""
    vm = IOLDockerVM(
        "iol-xe-1", str(uuid.uuid4()), compute_project, manager, "iol-xe/iol-xe:17-18-02",
        console_type=console_type, environment=environment,
        extra_volumes=extra_volumes or [], adapters=adapters,
    )
    vm._cid = "e90e34656842"
    return vm


def _mock_start(vm, state="stopped"):
    """Mock everything DockerVM.start() needs besides the runtime prep."""
    vm._get_container_state = AsyncioMagicMock(return_value=state)
    vm._start_ubridge = AsyncioMagicMock()
    vm._get_namespace = AsyncioMagicMock(return_value=42)
    vm._add_ubridge_connection = AsyncioMagicMock()
    vm._start_console_server = AsyncioMagicMock()


def _seed_proc(stdout=b"seedcid\n", returncode=0):
    proc = MagicMock()
    proc.communicate = AsyncioMagicMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


@pytest.fixture(autouse=True)
def runtime_dir(tmp_path, monkeypatch):
    """
    Point the unix-socket runtime directory at a per-test temporary path so
    the wiring/mount tests never create per-node directories in the real one.
    """
    rt = tmp_path / "run"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(rt))
    return rt


def _mock_wiring(vm):
    """Mock everything _add_ubridge_connection's unix-NIO path needs."""
    vm._ubridge_hypervisor = MagicMock()


def _wiring_dir(vm):
    """The per-node socket directory this VM wires through."""
    return os.path.join(os.environ["XDG_RUNTIME_DIR"], "gns3", "unixio", vm.id)


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------

def test_factory_selects_iol_for_env_marker(manager):

    assert manager._select_node_class(console_type="telnet",
                                      environment="GNS3_IOL_RUNNER=1") is IOLDockerVM


def test_factory_tolerates_whitespace_and_comma(manager):

    assert manager._select_node_class(console_type="telnet",
                                      environment=" GNS3_IOL_RUNNER=1,\nFOO=bar") is IOLDockerVM


def test_factory_docker_exec_wins_over_iol_marker(manager):

    assert manager._select_node_class(console_type="docker_exec",
                                      environment="GNS3_IOL_RUNNER=1") is VendorDockerVM


def test_factory_plain_environment_is_base(manager):

    assert manager._select_node_class(console_type="telnet",
                                      environment="FOO=bar\nGNS3_BAZ=nope") is DockerVM


def test_factory_generic_unix_knob_selects_vendor(manager):

    assert manager._select_node_class(console_type="telnet",
                                      environment="GNS3_UNIX_SOCKET_NIO=1") is VendorDockerVM


# ---------------------------------------------------------------------------
# Knob parsing
# ---------------------------------------------------------------------------

def test_marker_forces_skip_init_and_unix_nio(compute_project, manager):

    vm = _make_vm(compute_project, manager, environment="GNS3_IOL_RUNNER=1")
    assert vm._gns3_init is False
    assert vm._unix_socket_nio is True
    assert vm._unix_socket_dir == "/tmp"
    assert vm._iol_memory == 2048


def test_iol_memory_knob(compute_project, manager):

    vm = _make_vm(compute_project, manager,
                  environment="GNS3_IOL_RUNNER=1\nGNS3_IOL_MEMORY=4096")
    assert vm._iol_memory == 4096

    vm = _make_vm(compute_project, manager,
                  environment="GNS3_IOL_RUNNER=1\nGNS3_IOL_MEMORY=notanumber")
    assert vm._iol_memory == 2048


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_keeps_image_entrypoint(compute_project, manager):

    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "iol-xe"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=_create_response()) as mock:
            with patch("asyncio.subprocess.create_subprocess_exec",
                       return_value=_seed_proc()):
                vm = _make_vm(compute_project, manager)
                await vm.create()
                sent = mock.call_args.kwargs["data"]
                # the iol-runner entrypoint runs as PID 1, untouched
                assert sent["Entrypoint"] == IOL_ENTRYPOINT
                assert sent["Cmd"] == []


@pytest.mark.asyncio
async def test_create_auto_adds_config_and_tmp_run_volumes(compute_project, manager):

    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "iol-xe"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=_create_response()) as mock:
            with patch("asyncio.subprocess.create_subprocess_exec",
                       return_value=_seed_proc()):
                vm = _make_vm(compute_project, manager, extra_volumes=[])
                await vm.create()
                sent = mock.call_args.kwargs["data"]
                mounts = sent["HostConfig"]["Mounts"]
                targets = [m["Target"] for m in mounts if m.get("Type") == "bind"]
                # /config (runner config) and /tmp/run (startup-config + NVRAM)
                # are forced and bound at their real in-container paths
                # (skip-init retargeting); /tmp is the ephemeral runtime-dir
                # bind holding the netiomux sockets
                assert "/config" in targets
                assert "/tmp/run" in targets
                tmp_mounts = [m for m in mounts if m["Target"] == "/tmp"]
                assert len(tmp_mounts) == 1
                assert tmp_mounts[0]["Source"] == _wiring_dir(vm)
                assert not any(t.startswith("/gns3volumes/") for t in targets)
                vol_env = [v for v in sent["Env"] if v.startswith("GNS3_VOLUMES=")][0]
                assert "/config" in vol_env and "/tmp/run" in vol_env


@pytest.mark.asyncio
async def test_create_start_command_becomes_runner_flags(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    vm.start_command = "-keep"
    with asyncio_patch("gns3server.compute.docker.Docker.list_images",
                       return_value=[{"image": "iol-xe"}]):
        with asyncio_patch("gns3server.compute.docker.Docker.query",
                           return_value=_create_response()) as mock:
            with patch("asyncio.subprocess.create_subprocess_exec",
                       return_value=_seed_proc()):
                await vm.create()
                sent = mock.call_args.kwargs["data"]
                # start_command is the container CMD = extra iol-runner flags
                assert sent["Cmd"] == ["-keep"]


# ---------------------------------------------------------------------------
# start() — runtime preparation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_writes_iol_config(compute_project, manager):

    vm = _make_vm(compute_project, manager, adapters=4)
    _mock_start(vm)
    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    with open(os.path.join(vm.working_dir, "config", "iol-config.json")) as f:
        config = json.load(f)
    assert config["binary"] == "/binary.iol"
    assert config["num-eth"] == 16  # 4 adapters, each a 4-port unit
    assert config["num-serial"] == 0
    assert config["local-app"] == int(vm.id.replace("-", ""), 16) % 1022 + 1
    assert config["remote-app"] == 1023
    assert config["memory"] == 2048
    assert config["user-id"] == os.getuid()
    assert config["group-id"] == os.getgid()
    assert vm.status == "started"


def test_local_app_is_distinct_per_node(compute_project, manager):
    # IOL derives interface MACs from the app ID: two nodes sharing one would
    # drop each other's frames as MAC loops, so IDs must differ per node.
    vm1 = _make_vm(compute_project, manager)
    vm2 = _make_vm(compute_project, manager)
    id1 = int(vm1.id.replace("-", ""), 16) % 1022 + 1
    id2 = int(vm2.id.replace("-", ""), 16) % 1022 + 1
    assert id1 != id2
    assert 1 <= id1 <= 1022 and 1 <= id2 <= 1022


@pytest.mark.asyncio
async def test_start_rewrites_config_on_adapter_change(compute_project, manager):

    vm = _make_vm(compute_project, manager, adapters=4)
    _mock_start(vm)
    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    vm.adapters = 8
    _mock_start(vm)
    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    with open(os.path.join(vm.working_dir, "config", "iol-config.json")) as f:
        assert json.load(f)["num-eth"] == 32  # 8 adapters × 4 ports


@pytest.mark.asyncio
async def test_start_creates_run_dir(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    _mock_start(vm)
    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    assert os.path.isdir(os.path.join(vm.working_dir, "tmp", "run"))


@pytest.mark.asyncio
async def test_start_cleans_stale_sockets_but_keeps_run(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    for name in ("s00.sock", "c00.sock", "s01.sock", "c01.sock"):
        open(os.path.join(wiring_dir, name), "w").close()
    os.makedirs(os.path.join(wiring_dir, "netio1000"))
    run_dir = os.path.join(vm.working_dir, "tmp", "run")
    os.makedirs(run_dir, exist_ok=True)
    open(os.path.join(run_dir, "nvram_00001"), "w").close()
    open(os.path.join(run_dir, "config"), "w").close()

    _mock_start(vm, state="stopped")
    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    assert glob.glob(os.path.join(wiring_dir, "s??.sock")) == []
    assert glob.glob(os.path.join(wiring_dir, "c??.sock")) == []
    assert not os.path.exists(os.path.join(wiring_dir, "netio1000"))
    # the persistent runtime survives the cleanup
    assert os.path.exists(os.path.join(run_dir, "nvram_00001"))
    assert os.path.exists(os.path.join(run_dir, "config"))


@pytest.mark.asyncio
async def test_start_skips_cleanup_when_already_running(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    open(os.path.join(wiring_dir, "s00.sock"), "w").close()

    _mock_start(vm, state="running")
    with patch("gns3server.compute.docker.Docker.install_busybox"):
        with asyncio_patch("gns3server.compute.docker.Docker.query"):
            await vm.start()

    # live runner sockets must not be deleted behind its back
    assert os.path.exists(os.path.join(wiring_dir, "s00.sock"))


@pytest.mark.asyncio
async def test_fix_permissions_is_noop(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    with patch("asyncio.subprocess.create_subprocess_exec") as mock_exec:
        await vm._fix_permissions()
        mock_exec.assert_not_called()
    assert vm._permissions_fixed is True


@pytest.mark.asyncio
async def test_restart_is_graceful_stop_then_start(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    vm.stop = AsyncioMagicMock()
    vm.start = AsyncioMagicMock()
    await vm.restart()
    vm.stop.assert_called_once_with(graceful=True)
    vm.start.assert_called_once()


# ---------------------------------------------------------------------------
# Wiring — unix-socket NIO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_ubridge_connection_unix_wiring(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    _mock_wiring(vm)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    # the runner's receive socket must exist (created by the container)
    open(os.path.join(wiring_dir, "s00.sock"), "w").close()

    nio = manager.create_nio({"type": "nio_udp", "lport": 4242, "rport": 4343, "rhost": "127.0.0.1"})
    await vm._add_ubridge_connection(nio, 0)

    sent = [c for c in vm._ubridge_hypervisor.method_calls if "send" in str(c)]
    flat = "\n".join(str(c) for c in sent)
    local_sock = os.path.join(wiring_dir, "c00.sock")
    remote_sock = os.path.join(wiring_dir, "s00.sock")
    assert call.send("bridge create bridge0") in sent
    assert call.send(f'bridge add_nio_unix bridge0 "{local_sock}" "{remote_sock}"') in sent
    assert "add_nio_udp bridge0 4242 127.0.0.1 4343" in flat
    assert "bridge start bridge0" in flat
    # the TAP/namespace path must not be used at all
    assert "add_nio_tap" not in flat
    assert "move_to_ns" not in flat
    assert "set_mac_addr" not in flat
    assert vm._ethernet_adapters[0].host_ifc == local_sock


@pytest.mark.asyncio
async def test_add_ubridge_connection_stale_local_socket_unlinked(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    _mock_wiring(vm)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    open(os.path.join(wiring_dir, "c00.sock"), "w").close()
    open(os.path.join(wiring_dir, "s00.sock"), "w").close()

    await vm._add_ubridge_connection(None, 0)
    assert not os.path.exists(os.path.join(wiring_dir, "c00.sock"))


@pytest.mark.asyncio
async def test_add_ubridge_connection_adapter_out_of_range(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    _mock_wiring(vm)
    with pytest.raises(DockerError):
        await vm._add_ubridge_connection(None, 42)


@pytest.mark.asyncio
async def test_add_ubridge_connection_timeout_is_actionable(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    _mock_wiring(vm)
    vm._stop_ubridge = AsyncioMagicMock()

    async def raise_timeout(path, timeout=60):
        raise asyncio.TimeoutError()

    with patch("gns3server.compute.docker.vendor_docker_vm.wait_for_file_creation",
               side_effect=raise_timeout):
        with pytest.raises(DockerError) as excinfo:
            await vm._add_ubridge_connection(None, 0)
    # the message names the adapter and the exact wiring path
    assert "adapter 0" in str(excinfo.value)
    assert "s00.sock" in str(excinfo.value)
    # uBridge must not survive a half-wired bridge: the retry would fail
    # with "bridge already exist"
    assert vm._stop_ubridge.called


@pytest.mark.asyncio
async def test_add_ubridge_connection_without_nio_still_wires(compute_project, manager):

    vm = _make_vm(compute_project, manager)
    _mock_wiring(vm)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    open(os.path.join(wiring_dir, "s00.sock"), "w").close()

    await vm._add_ubridge_connection(None, 0)
    flat = "\n".join(str(c) for c in vm._ubridge_hypervisor.method_calls)
    assert "bridge create bridge0" in flat
    assert "add_nio_unix" in flat
    # no link yet: no UDP NIO, no bridge start (matches base semantics)
    assert "add_nio_udp" not in flat
    assert "bridge start" not in flat


# ---------------------------------------------------------------------------
# IOU-style port model — 1 adapter = 4 ethernet ports
# ---------------------------------------------------------------------------

def test_adapters_are_four_port_units(compute_project, manager):

    vm = _make_vm(compute_project, manager, adapters=2)
    assert vm.adapters == 2
    assert len(vm._ethernet_adapters) == 2
    for adapter in vm._ethernet_adapters:
        assert adapter.interfaces == 4
        for port_number in range(4):
            assert adapter.port_exists(port_number)
        assert not adapter.port_exists(4)


@pytest.mark.asyncio
async def test_wiring_addresses_ports_within_adapters(compute_project, manager):

    vm = _make_vm(compute_project, manager, adapters=2)
    _mock_wiring(vm)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    # adapter 1, port 2 -> flat interface index 6 (1 * 4 + 2)
    open(os.path.join(wiring_dir, "s06.sock"), "w").close()

    nio = manager.create_nio({"type": "nio_udp", "lport": 4242, "rport": 4343, "rhost": "127.0.0.1"})
    await vm._add_ubridge_connection(nio, 1, port_number=2)

    flat = "\n".join(str(c) for c in vm._ubridge_hypervisor.method_calls)
    assert 'bridge add_nio_unix bridge1_2 ' in flat
    assert f'"{os.path.join(wiring_dir, "c06.sock")}"' in flat
    assert f'"{os.path.join(wiring_dir, "s06.sock")}"' in flat
    assert "add_nio_udp bridge1_2 4242 127.0.0.1 4343" in flat
    assert vm._ethernet_adapters[1].host_ifc == os.path.join(wiring_dir, "c06.sock")


@pytest.mark.asyncio
async def test_nio_binding_rejects_port_out_of_range(compute_project, manager):

    vm = _make_vm(compute_project, manager, adapters=2)
    nio = manager.create_nio({"type": "nio_udp", "lport": 4242, "rport": 4343, "rhost": "127.0.0.1"})
    with pytest.raises(DockerError) as excinfo:
        await vm.adapter_add_nio_binding(1, nio, port_number=4)
    assert "Port 4" in str(excinfo.value)

    with pytest.raises(DockerError):
        await vm.adapter_add_nio_binding(9, nio, port_number=0)


# ---------------------------------------------------------------------------
# Generic GNS3_UNIX_SOCKET_NIO knob on plain VendorDockerVM
# ---------------------------------------------------------------------------

def test_env_unix_socket_nio_parsing(compute_project, manager):

    vm = VendorDockerVM("vendor-1", str(uuid.uuid4()), compute_project, manager, "vendor:latest",
                        console_type="docker_exec",
                        environment="GNS3_SKIP_INIT=1\nGNS3_UNIX_SOCKET_NIO=1\nGNS3_UNIX_SOCKET_DIR=/var/run/socks")
    assert vm._unix_socket_nio is True
    assert vm._unix_socket_dir == "/var/run/socks"

    # invalid dirs are rejected, keeping the default
    vm = VendorDockerVM("vendor-1", str(uuid.uuid4()), compute_project, manager, "vendor:latest",
                        console_type="docker_exec",
                        environment="GNS3_SKIP_INIT=1\nGNS3_UNIX_SOCKET_NIO=yes\nGNS3_UNIX_SOCKET_DIR=../../etc")
    assert vm._unix_socket_nio is True
    assert vm._unix_socket_dir == "/tmp"

    # off by default / explicit off
    vm = VendorDockerVM("vendor-1", str(uuid.uuid4()), compute_project, manager, "vendor:latest",
                        console_type="docker_exec", environment="GNS3_SKIP_INIT=1")
    assert vm._unix_socket_nio is False


def test_unix_socket_dir_bound_from_runtime_dir(compute_project, manager):

    vm = VendorDockerVM("vendor-1", str(uuid.uuid4()), compute_project, manager, "vendor:latest",
                        console_type="docker_exec",
                        environment="GNS3_SKIP_INIT=1\nGNS3_UNIX_SOCKET_NIO=1",
                        extra_volumes=[])
    # the socket directory is an ephemeral per-node directory from the
    # runtime dir, not a volume: writable by the (unprivileged) agent and
    # short enough for AF_UNIX
    binds = vm._mount_binds({"Config": {"Volumes": {}}})
    socket_binds = [b for b in binds if b.get("Target") == "/tmp"]
    assert len(socket_binds) == 1
    assert socket_binds[0]["Type"] == "bind"
    assert socket_binds[0]["Source"] == _wiring_dir(vm)

    # a socket dir already covered by a persisted volume gets no extra bind
    vm = VendorDockerVM("vendor-1", str(uuid.uuid4()), compute_project, manager, "vendor:latest",
                        console_type="docker_exec",
                        environment="GNS3_SKIP_INIT=1\nGNS3_UNIX_SOCKET_NIO=1",
                        extra_volumes=["/tmp"])
    binds = vm._mount_binds({"Config": {"Volumes": {}}})
    assert not any(b.get("Source") == _wiring_dir(vm) for b in binds)
    assert any(b.get("Target") == "/tmp" for b in binds)


@pytest.mark.asyncio
async def test_generic_unix_socket_dir_honored_in_wiring(compute_project, manager):

    vm = VendorDockerVM("vendor-1", str(uuid.uuid4()), compute_project, manager, "vendor:latest",
                        console_type="docker_exec",
                        environment="GNS3_SKIP_INIT=1\nGNS3_UNIX_SOCKET_NIO=1\nGNS3_UNIX_SOCKET_DIR=/var/run/socks")
    _mock_wiring(vm)
    wiring_dir = _wiring_dir(vm)
    os.makedirs(wiring_dir, exist_ok=True)
    open(os.path.join(wiring_dir, "s00.sock"), "w").close()

    await vm._add_ubridge_connection(None, 0)
    flat = "\n".join(str(c) for c in vm._ubridge_hypervisor.method_calls)
    assert f'"{os.path.join(wiring_dir, "s00.sock")}"' in flat
    assert "add_nio_tap" not in flat
