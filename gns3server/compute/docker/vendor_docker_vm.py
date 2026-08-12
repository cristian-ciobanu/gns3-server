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
Vendor NOS Docker container subclass.

Provides support for vendor NOS containers (Nokia SR Linux, Arista cEOS,
Juniper cRPD, …) whose CLI is a separate TUI process not exposed on PID 1
stdio, and whose boot model requires skipping GNS3's init.sh bootstrapping.

The subclass is selected automatically when ``console_type == "docker_exec"``.
All vendor features are opt-in — without GNS3_* environment variables the
container behaves identically to DockerVM.
"""

import asyncio
import json
import logging
import os
import stat

from gns3server.utils.asyncio.telnet_server import AsyncioTelnetServer
from gns3server.compute.docker.docker_vm import DockerVM
from gns3server.compute.docker.docker_error import DockerError

log = logging.getLogger(__name__)


class VendorDockerVM(DockerVM):
    """
    DockerVM subclass for vendor NOS containers.

    Opt-in features, activated by GNS3_-prefixed environment entries
    (host-side only — GNS3_ entries are never forwarded into the container):

    * ``GNS3_SKIP_INIT=1`` — do not prepend /gns3/init.sh; the container runs
      its own entrypoint (e.g. SR Linux's ``sr_linux``). Init.sh's volume
      persistence (bind-mount /gns3volumes → target) is replicated via
      ``docker exec`` after the container starts.
    * ``GNS3_INTERFACE_NAMES=mgmt0,e1-1,e1-2`` — rename injected interfaces
      (adapter order) instead of default ``eth{N}``.
    * ``GNS3_CONSOLE_CMD=/opt/srlinux/bin/sr_cli`` — command run inside the
      container by the ``docker_exec`` console (defaults to ``/bin/sh``).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Prototype knobs — parsed from GNS3_-prefixed entries in create().
        # Parse eagerly so _get_container_ifname can return the right name.
        self._gns3_init = True
        self._interface_names = []
        self._console_cmd = None
        self._console_exec_writer = None

        if self._environment:
            for _line in self._environment.splitlines():
                _line = _line.strip().rstrip(",")
                if _line.startswith("GNS3_SKIP_INIT="):
                    self._gns3_init = _line.split("=", 1)[1].strip().lower() not in ("1", "true", "yes")
                elif _line.startswith("GNS3_INTERFACE_NAMES="):
                    self._interface_names = [
                        n.strip() for n in _line.split("=", 1)[1].split(",") if n.strip()
                    ]
                elif _line.startswith("GNS3_CONSOLE_CMD="):
                    self._console_cmd = _line.split("=", 1)[1].strip()

    # ---- hook overrides ---------------------------------------------------

    def _prepare_init_and_interface_env(self, params):
        """
        Override: conditionally prepend init.sh, and honour
        GNS3_INTERFACE_NAMES (if set) for GNS3_MAX_ETHERNET.
        """
        if self._gns3_init:
            params["Entrypoint"].insert(0, "/gns3/init.sh")

        # Tell init.sh which last interface to wait for; honour the rename if any
        # (no-op when init is skipped, but kept consistent).
        if self._interface_names and self.adapters - 1 < len(self._interface_names):
            last_ifname = self._interface_names[self.adapters - 1]
        else:
            last_ifname = f"eth{self.adapters - 1}"
        params["Env"].append(f"GNS3_MAX_ETHERNET={last_ifname}")

    def _get_container_ifname(self, adapter_number):
        """
        Override: honour GNS3_INTERFACE_NAMES (e.g. mgmt0, e1-1) in adapter
        order; fall back to eth{N} for unlisted ports.
        """
        if self._interface_names and adapter_number < len(self._interface_names):
            return self._interface_names[adapter_number]
        return f"eth{adapter_number}"

    def _cleanup_console_resources(self):
        """
        Override: close the docker-exec pty socket, if any, so the next
        restart or stop doesn't leak it.
        """
        if self._console_exec_writer:
            try:
                self._console_exec_writer.close()
            except Exception:
                pass
            self._console_exec_writer = None

    async def start(self):
        await super().start()
        if self.status == "started" and not self._gns3_init:
            await self._setup_skip_init_volumes()
            # Fix host-side ownership of the seeded volume right away so the
            # controller can read project files while the node runs. Reset the
            # "fixed" flag afterwards: files written by the container during
            # runtime still need the stop-time pass.
            await self._fix_permissions()
            self._permissions_fixed = False

    async def _fix_permissions(self):
        """
        Host-side override of DockerVM._fix_permissions for SKIP_INIT
        containers. The persistent volumes are Docker bind mounts of
        directories under the node's project directory, so ownership is fixed
        directly on the host — no docker exec, no container restart required
        (the base implementation restarts an exited container just to chown,
        which is wasteful for vendor NOS images).

        Two passes per volume, mirroring the base/busybox behaviour:

        1. record each entry's container-visible mode/uid/gid into
           `.gns3_perms` (same `mode:uid:gid:path` format init.sh consumes,
           paths are in-container absolute so the restore inside the
           container resolves them);
        2. chmod u+rX + chown to the host user so the GNS3 process can read
           and delete files from the project directory.
        """
        uid, gid = os.getuid(), os.getgid()
        for volume in self._volumes:
            path = os.path.join(self.working_dir, os.path.relpath(volume, "/"))
            if not os.path.isdir(path):
                continue

            def onerror(exc):
                log.debug("Could not walk '%s' for container '%s': %s", exc.filename, self._name, exc)

            # 1. record container-visible permissions for restore at next start
            try:
                with open(os.path.join(path, ".gns3_perms"), "w") as perms_file:
                    for root, dirs, files in os.walk(path, onerror=onerror):
                        for entry in dirs + files:
                            entry_path = os.path.join(root, entry)
                            try:
                                st = os.lstat(entry_path)
                            except OSError:
                                continue
                            container_path = os.path.join(volume, os.path.relpath(entry_path, path))
                            perms_file.write(
                                f"{stat.S_IMODE(st.st_mode):o}:{st.st_uid}:{st.st_gid}:{container_path}\n"
                            )
            except OSError as e:
                log.warning(
                    "Could not record permissions for '%s' on container '%s': %s", path, self._name, e
                )
                continue

            # 2. chmod u+rX + chown to the host user
            for root, dirs, files in os.walk(path, onerror=onerror):
                for entry in dirs + files:
                    entry_path = os.path.join(root, entry)
                    try:
                        st = os.lstat(entry_path)
                        is_link = stat.S_ISLNK(st.st_mode)
                        if not is_link:
                            mode = stat.S_IMODE(st.st_mode)
                            new_mode = mode | 0o400  # u+r
                            if stat.S_ISDIR(st.st_mode) or (mode & 0o111):  # u+X
                                new_mode |= 0o100
                            os.chmod(entry_path, new_mode)
                        os.lchown(entry_path, uid, gid)
                    except OSError as e:
                        log.debug(
                            "Could not fix permissions on '%s' for container '%s': %s",
                            entry_path, self._name, e,
                        )
        self._permissions_fixed = True

    async def _setup_skip_init_volumes(self):
        """
        Replicate the volume-persistence portion of init.sh (lines 35–52) for
        containers that skip init.sh (GNS3_SKIP_INIT=1).

        On first start the container's original files are seeded into the
        persistent host directory; on subsequent starts the persisted data
        is bind-mounted over the in-container path so writes land on the host.
        Permission-changes recorded by _fix_permissions at the previous
        stop are restored (best-effort).
        """
        for volume in self._volumes:
            vol_target = f"/gns3volumes{volume}"
            # fmt: off
            script = (
                f'mkdir -p "{volume}" && '
                f'if [ ! -f "{vol_target}/.gns3_perms" ]; then '
                f'  /gns3/bin/busybox cp -a "{volume}/." "{vol_target}/" 2>/dev/null; '
                f'  /gns3/bin/busybox touch "{vol_target}/.gns3_perms"; '
                f'fi && '
                f'/gns3/bin/busybox mount --bind "{vol_target}" "{volume}" && '
                f'while IFS=: read -r PERMS OWNER GROUP FILE; do '
                f'  [ -L "$FILE" ] || /gns3/bin/busybox chmod "$PERMS" "$FILE" 2>/dev/null; '
                f'  /gns3/bin/busybox chown -h "$OWNER:$GROUP" "$FILE" 2>/dev/null; '
                f'done < "{volume}/.gns3_perms"'
            )
            # fmt: on
            try:
                process = await asyncio.subprocess.create_subprocess_exec(
                    "docker",
                    "exec",
                    self._cid,
                    "sh",
                    "-c",
                    script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    err = stderr.decode(errors="replace").strip()
                    log.warning(
                        "Volume setup for '%s' on container '%s' returned %d: %s",
                        volume, self._name, process.returncode, err,
                    )
                else:
                    log.info("Volume '%s' bound to persistent storage for '%s'", volume, self._name)
            except OSError as e:
                log.warning(
                    "Could not setup volume '%s' for container '%s': %s", volume, self._name, e
                )

    async def _start_console_server(self):
        """
        Override: add the ``docker_exec`` console type alongside the
        telnet/ssh/http types supported by the base class.
        """
        if self.console_type == "docker_exec":
            await self._start_docker_exec_console()
        else:
            await super()._start_console_server()

    # ---- docker_exec console implementation --------------------------------

    async def _start_docker_exec_console(self):
        """
        Start a console that runs a command inside the container via the Docker
        exec API, bridged to a telnet server. Intended for vendor NOS containers
        (e.g. Nokia SR Linux) whose CLI is a separate TUI process not exposed on
        PID 1's stdio.

        The exec is created lazily on the first client connection (not when the
        node starts) so the command's startup terminal probe has a real xterm.js
        client to answer it (CPR / prompt_toolkit). The single exec is then
        shared (broadcast) by all clients, matching GNS3's console model.
        Command from GNS3_CONSOLE_CMD.
        """

        command = self._console_cmd or "/bin/sh"
        vm = self
        manager = self.manager
        cid = self._cid

        class _LazyExecTelnetServer(AsyncioTelnetServer):
            """Telnet console whose docker exec (pty + command) is created on the
            first client connection and then broadcast to all clients."""

            def __init__(srv):
                super().__init__(
                    reader=None,
                    writer=None,
                    binary=True,
                    echo=False,
                    naws=True,
                    window_size_changed_callback=srv._on_naws,
                )
                srv._exec_id = None
                srv._started = False
                srv._lock = asyncio.Lock()
                srv._log_name = f"docker_exec console '{vm.name}'"

            async def _on_naws(srv, columns, rows):
                if srv._exec_id:
                    try:
                        await manager.query(
                            "POST",
                            f"exec/{srv._exec_id}/resize",
                            params={"h": str(rows), "w": str(columns)},
                        )
                    except DockerError:
                        pass

            async def run(srv, network_reader, network_writer):
                """Catch and log any exception that kills the client session."""
                try:
                    await super().run(network_reader, network_writer)
                except Exception as exc:
                    log.warning(f"{srv._log_name}: client session terminated: {exc}", exc_info=True)

            async def _create_exec(srv):
                # create exec with a pty; run as root (vendor CLIs reject the
                # image's default unprivileged user) and export TERM=xterm.
                result = await manager.query(
                    "POST",
                    f"containers/{cid}/exec",
                    data={
                        "AttachStdin": True,
                        "AttachStdout": True,
                        "AttachStderr": True,
                        "Tty": True,
                        "User": "root",
                        "Env": ["TERM=xterm"],
                        "Cmd": ["sh", "-c", f"while true; do {command}; done"],
                    },
                )
                srv._exec_id = result["Id"]
                log.info(f"{srv._log_name}: exec created ({srv._exec_id})")

                # start the exec via a hijacked raw HTTP request on the Docker
                # unix socket; with Tty:true the response body is a raw
                # bidirectional pty byte stream (no multiplexing).
                reader, writer = await asyncio.open_unix_connection(manager._server_url)
                body = json.dumps({"Detach": False, "Tty": True})
                request = (
                    f"POST /v{manager._api_version}/exec/{srv._exec_id}/start HTTP/1.1\r\n"
                    "Host: docker\r\n"
                    "Connection: Upgrade\r\n"
                    "Upgrade: tcp\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n{body}"
                ).encode()
                writer.write(request)
                await writer.drain()
                try:
                    headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
                    writer.close()
                    raise DockerError(f"Docker exec start failed: {e}")
                status_line = headers.split(b"\r\n", 1)[0]
                log.info(f"{srv._log_name}: hijacked start -> {status_line.decode(errors='ignore')}")
                if b" 101 " not in status_line and b" 200 " not in status_line:
                    writer.close()
                    raise DockerError(f"Docker exec start rejected: {status_line.decode(errors='ignore')}")

                # wire the exec stream as this server's upstream and start the
                # broadcast task. AsyncioTelnetServer.start() only starts the
                # broadcast when a reader is set at construction time, so with a
                # lazy upstream we start it manually here.
                srv._reader = reader
                srv._writer = writer
                vm._console_exec_writer = writer  # for stop() cleanup
                srv._broadcast_task = asyncio.create_task(srv._broadcast_from_upstream())
                log.info(f"{srv._log_name}: broadcast task started, upstream wired, ready")

            async def client_connected_hook(srv):
                await super().client_connected_hook()
                log.info(f"{srv._log_name}: client connected, lazy_started={srv._started}")
                async with srv._lock:
                    if not srv._started:
                        try:
                            await srv._create_exec()
                        except Exception as exc:
                            log.warning(f"{srv._log_name}: failed to create exec: {exc}", exc_info=True)
                            raise
                        srv._started = True
                        try:
                            await srv._on_naws(80, 24)  # initial size before NAWS
                        except Exception:
                            pass
                # ask the TUI to (re)draw for the client that just connected.
                if srv._writer:
                    try:
                        srv._writer.write(b"\x0c")  # Ctrl-L -> TUI redraws
                        await srv._writer.drain()
                    except Exception as exc:
                        log.warning(f"{srv._log_name}: Ctrl-L write failed: {exc}")
                log.info(f"{srv._log_name}: client_connected_hook done")

        telnet = _LazyExecTelnetServer()
        try:
            self._telnet_servers.append(
                await telnet.start(self._manager.port_manager.console_host, self.console)
            )
        except OSError as e:
            raise DockerError(
                f"Could not start console server on socket {self._manager.port_manager.console_host}:{self.console}: {e}"
            )
        log.debug(f"Docker container '{self.name}' started docker_exec console (lazy) on {self.console}")
