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
from gns3server.compute.docker.vendor_docker_vm import VendorDockerVM, _LazyExecTelnetServer
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


# ---------------------------------------------------------------------------
# _LazyExecTelnetServer — upstream aliveness + reconnect/recreate logic
# ---------------------------------------------------------------------------

def _make_lazy_server(compute_project, manager):
    """Build a _LazyExecTelnetServer with _create_exec mocked out (no docker)."""
    vm = _make_vm(compute_project, manager, environment="GNS3_CONSOLE_CMD=/opt/srlinux/bin/sr_cli")
    srv = _LazyExecTelnetServer(vm, manager, "e90e34656842", "/opt/srlinux/bin/sr_cli")
    srv._create_exec = AsyncioMagicMock()
    srv._on_naws = AsyncioMagicMock()
    return srv


def _live_writer():
    """A writer mock that reports as open (not closing)."""
    w = MagicMock()
    w.is_closing.return_value = False
    return w


def _dead_writer():
    """A writer mock that reports as closing (pty closed)."""
    w = MagicMock()
    w.is_closing.return_value = True
    return w


def test_upstream_alive_never_created(compute_project, manager):

    srv = _make_lazy_server(compute_project, manager)
    assert srv._upstream_alive() is False


def test_upstream_alive_writer_closing(compute_project, manager):

    srv = _make_lazy_server(compute_project, manager)
    srv._exec_id = "abc"
    srv._writer = _dead_writer()
    srv._broadcast_task = MagicMock()
    srv._broadcast_task.done.return_value = False
    assert srv._upstream_alive() is False


def test_upstream_alive_broadcast_done(compute_project, manager):

    srv = _make_lazy_server(compute_project, manager)
    srv._exec_id = "abc"
    srv._writer = _live_writer()
    srv._broadcast_task = MagicMock()
    srv._broadcast_task.done.return_value = True  # CLI exited → EOF → task ended
    assert srv._upstream_alive() is False


def test_upstream_alive_live(compute_project, manager):

    srv = _make_lazy_server(compute_project, manager)
    srv._exec_id = "abc"
    srv._writer = _live_writer()
    srv._broadcast_task = MagicMock()
    srv._broadcast_task.done.return_value = False
    assert srv._upstream_alive() is True


@pytest.mark.asyncio
async def test_first_connect_creates_exec(compute_project, manager):

    srv = _make_lazy_server(compute_project, manager)
    # never created → must create
    await srv.client_connected_hook()
    srv._create_exec.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_live_exec_not_recreated(compute_project, manager):
    """Reconnecting while the exec is alive must NOT recreate it."""

    srv = _make_lazy_server(compute_project, manager)
    srv._exec_id = "abc"
    srv._writer = _live_writer()
    srv._broadcast_task = MagicMock()
    srv._broadcast_task.done.return_value = False

    await srv.client_connected_hook()
    srv._create_exec.assert_not_called()
    # Ctrl-L redraw is still sent to the live writer
    srv._writer.write.assert_any_call(b"\x0c")


@pytest.mark.asyncio
async def test_reconnect_after_death_recreates_exec(compute_project, manager):
    """The core reconnect fix: after the CLI exits (broadcast task done),
    the next client connection recreates the exec so CPR gets answered."""

    srv = _make_lazy_server(compute_project, manager)
    # simulate a dead upstream: exec existed, but the pty closed / task ended
    srv._exec_id = "old-exec"
    srv._writer = _dead_writer()
    srv._broadcast_task = MagicMock()
    srv._broadcast_task.done.return_value = True

    await srv.client_connected_hook()
    srv._create_exec.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_closes_half_dead_writer(compute_project, manager):
    """If the writer is still open but the broadcast task died, the old writer
    must be closed before a new exec is created (no socket leak)."""

    srv = _make_lazy_server(compute_project, manager)
    srv._exec_id = "old-exec"
    srv._writer = _live_writer()  # still open, but...
    srv._broadcast_task = MagicMock()
    srv._broadcast_task.done.return_value = True  # ...task ended

    await srv.client_connected_hook()
    srv._writer.close.assert_called_once()
    srv._create_exec.assert_called_once()


@pytest.mark.asyncio
async def test_create_exec_cmd_has_no_while_true(compute_project, manager):
    """The command must NOT be wrapped in a while-true loop (regression guard:
    while-true restarts the CLI with no client to answer CPR → blank screen)."""

    vm = _make_vm(compute_project, manager)
    manager._server_url = "/var/run/docker.sock"
    manager._api_version = "1.40"
    srv = _LazyExecTelnetServer(vm, manager, "e90e34656842", "/opt/srlinux/bin/sr_cli")

    captured = {}

    async def fake_query(method, path, data=None, **kw):
        captured["data"] = data
        return {"Id": "exec123"}

    manager.query = fake_query

    with patch("asyncio.open_unix_connection") as mock_open:
        reader = MagicMock()
        reader.readuntil = AsyncioMagicMock(return_value=b"HTTP/1.1 101 Upgraded\r\n\r\n")
        writer = MagicMock()
        writer.is_closing.return_value = False
        mock_open.return_value = (reader, writer)
        await srv._create_exec()

    cmd = captured["data"]["Cmd"]
    assert cmd == ["sh", "-c", "/opt/srlinux/bin/sr_cli"]
    assert "while true" not in cmd[2]
    # must run as root with a pty and TERM
    assert captured["data"]["User"] == "root"
    assert captured["data"]["Tty"] is True
    assert "TERM=xterm" in captured["data"]["Env"]


# ---------------------------------------------------------------------------
# Container termination (graceful stop for vendor NOS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_terminate_container_graceful_stop(compute_project, manager):
    """Vendor containers must be SIGTERMed with a grace period, not SIGKILLed
    on the spot: systemd NOS images (e.g. Cisco XRd) require a graceful
    shutdown, and Docker itself SIGKILLs the container once the grace period
    expires."""

    vm = _make_vm(compute_project, manager)
    manager.query = AsyncioMagicMock()

    await vm._terminate_container()

    manager.query.assert_called_once_with("POST", "containers/e90e34656842/stop", params={"t": 60})


@pytest.mark.asyncio
async def test_terminate_container_already_stopped_is_silent(compute_project, manager):
    """Docker answers 304 when the container is already stopped — that is not
    an error for the stop path."""

    from gns3server.compute.docker.docker_error import DockerHttp304Error

    vm = _make_vm(compute_project, manager)
    manager.query = AsyncioMagicMock(
        side_effect=DockerHttp304Error("Docker has returned an error: 304"))
    await vm._terminate_container()  # must not raise


@pytest.mark.asyncio
async def test_stop_uses_graceful_termination(compute_project, manager):
    """The full stop() path must route through _terminate_container (the
    vendor override), not the base class' immediate kill."""

    vm = _make_vm(compute_project, manager)
    with patch.object(DockerVM, "_clean_servers", new=AsyncioMagicMock()):
        with patch.object(DockerVM, "_stop_ubridge", new=AsyncioMagicMock()):
            with patch.object(
                DockerVM, "_get_container_state", new=AsyncioMagicMock(return_value="running")
            ):
                with patch.object(
                    VendorDockerVM, "_fix_permissions", new=AsyncioMagicMock()
                ) as mock_perms:
                    mock_perms.return_value = None
                    vm._permissions_fixed = True
                    with patch.object(
                        VendorDockerVM, "_terminate_container", new=AsyncioMagicMock()
                    ) as mock_term:
                        await vm.stop()
    mock_term.assert_called_once()
