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
IOL (IOS on Linux) Docker container subclass.

Supports IOL images packaged with Cisco CML's container runner
(``iol-runner``, ``virl.lab/cmd/iol-runner``), e.g. ``iol-xe/iol-xe:17-18-02``:
a scratch image whose ENTRYPOINT is ``iol-runner -config /config/iol-config.json
-stdio``. The runner generates the license, writes the NETMAP, manages NVRAM
and muxes the IOS console onto PID 1 stdio (works with the plain ``telnet``
console type; requires a TTY, which GNS3 always allocates).

Networking does not use the container's network namespace at all: the runner's
netiomux exposes per-interface AF_UNIX datagram sockets in the container's
``/tmp`` (``s%02d.sock`` receive, ``c%02d.sock`` send — raw Ethernet frames),
wired by the generic ``GNS3_UNIX_SOCKET_NIO`` capability of VendorDockerVM
(uBridge reaches them through a per-node runtime directory bound at /tmp —
see ``VendorDockerVM._unix_socket_host_dir``). Because the netio bus
directory is private to the node, the application IDs are fixed constants
with no cross-node collisions.

This class is selected by the ``GNS3_IOL_RUNNER=1`` environment marker.
"""

import contextlib
import glob
import json
import logging
import os
import shutil

from gns3server.compute.docker.docker_error import DockerHttp404Error
from gns3server.compute.docker.vendor_docker_vm import VendorDockerVM

log = logging.getLogger(__name__)


class IOLDockerVM(VendorDockerVM):
    """
    VendorDockerVM subclass for iol-runner images.

    Extra opt-in knob (beyond the inherited vendor ones):

    * ``GNS3_IOL_MEMORY=<MB>`` — IOL router memory passed via the generated
      config (default 2048). The template ``memory`` field caps the whole
      container: keep it at IOL memory + ~512 MB headroom or the kernel
      OOM-killer will fire.

    The marker itself forces ``GNS3_SKIP_INIT`` and the unix-socket NIO wiring,
    and auto-adds the ``/config`` and ``/tmp/run`` persistent volumes, so a
    template containing only ``GNS3_IOL_RUNNER=1`` is fully configured.
    """

    _IOL_CONFIG_DIR = "/config"
    _IOL_RUN_DIR = "/tmp/run"

    def _parse_vendor_environment(self):

        super()._parse_vendor_environment()
        # The image has no shell (scratch): init.sh could neither run (its
        # #!/bin/sh shebang doesn't exist) nor wait for eth interfaces that
        # are never created. The console is IOS itself on PID 1 stdio.
        self._gns3_init = False
        self._unix_socket_nio = True
        self._unix_socket_dir = "/tmp"

        self._iol_memory = 2048
        if self._environment:
            for _line in self._environment.splitlines():
                _line = _line.strip().rstrip(",")
                if _line.startswith("GNS3_IOL_MEMORY="):
                    try:
                        memory = int(_line.split("=", 1)[1].strip())
                        if memory > 0:
                            self._iol_memory = memory
                    except ValueError:
                        pass

    def _persistent_volume_list(self, image_info, include_network_config=True):
        """
        Override: the runner requires ``/config`` (its config file, generated
        below) and ``/tmp/run`` (its working directory: startup-config and
        NVRAM live there — NETMAP and the netiomux sockets are ephemeral and
        stay in the container's own /tmp). Auto-add both so a minimal
        template cannot be misconfigured.
        """

        volumes = super()._persistent_volume_list(image_info, include_network_config)
        for needed in (self._IOL_CONFIG_DIR, self._IOL_RUN_DIR):
            if not any(needed == v or needed.startswith(v.rstrip("/") + "/") for v in volumes):
                volumes.append(needed)
        return volumes

    async def start(self):

        await self._prepare_iol_runtime()
        await super().start()

    async def restart(self):
        """
        Override: the base restart is a bare ``docker restart`` — the runner
        would read a stale config (no adapter-count/memory refresh) and
        uBridge would keep wiring to the previous run's sockets. Stop
        gracefully (SIGTERM lets the runner flush NVRAM) and start again.
        """

        await self.stop(graceful=True)
        await self.start()

    async def _prepare_iol_runtime(self):
        """
        Regenerate the node's runtime files before the container starts:

        * ``<working_dir>/tmp/run/`` must exist or the IOL process dies at
          boot (the runner writes NETMAP there but does not create it).
        * ``<working_dir>/config/iol-config.json`` is rewritten on every
          start so adapter-count and memory changes take effect.
        * Sockets and netio bus directories left in the wiring directory by a
          previous (possibly SIGKILLed) run are removed — the runner rebinds
          them on boot and would fail on a stale file.

        ``tmp/run`` (startup-config, NVRAM) is never touched. Neither is
        anything while the container is already running (idempotent start of
        a live node: the sockets belong to the running runner).
        """

        try:
            state = await self._get_container_state()
        except DockerHttp404Error:
            state = "stopped"

        os.makedirs(os.path.join(self.working_dir, "tmp", "run"), exist_ok=True)
        self._write_iol_config()

        if state == "running":
            return

        wiring_dir = self._unix_socket_wiring_dir()
        for pattern in ("s??.sock", "c??.sock"):
            for stale in glob.glob(os.path.join(wiring_dir, pattern)):
                with contextlib.suppress(OSError):
                    os.unlink(stale)
        for netio_dir in glob.glob(os.path.join(wiring_dir, "netio*")):
            shutil.rmtree(netio_dir, ignore_errors=True)

    def _write_iol_config(self):
        """
        Write the runner's config file on the host side of the /config volume.
        The runner drops to user-id/group-id after its setup, so everything it
        creates is owned by the server user — which is also what lets the
        (unprivileged) uBridge write into the node's socket directory.
        """

        config = {
            "binary": "/binary.iol",
            "memory": self._iol_memory,
            "num-eth": self.adapters,
            "num-serial": 0,  # GNS3 docker adapters are ethernet-only
            "local-app": 1,
            "remote-app": 2,
            "user-id": os.getuid(),
            "group-id": os.getgid(),
        }
        config_file = os.path.join(self.working_dir, "config", "iol-config.json")
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        log.debug("Wrote iol-runner config for '%s': %s", self._name, config)

    async def _fix_permissions(self):
        """
        Override: no-op. The generated config maps the runner to the server's
        uid/gid, so no root-owned files ever appear in the volumes, and this
        image has no shell for the container-side busybox pass anyway.
        """

        self._permissions_fixed = True
