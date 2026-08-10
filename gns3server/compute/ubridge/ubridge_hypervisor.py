#
# Copyright (C) 2015 GNS3 Technologies Inc.
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

import re
import time
import logging
import asyncio
import threading
import concurrent.futures

from gns3server.utils.asyncio import locking
from .ubridge_error import UbridgeError

log = logging.getLogger(__name__)

# Dedicated thread pool for blocking ubridge socket I/O.  Every node gets
# its own ubridge process + socket, so N nodes can send commands in true
# OS-thread parallelism.  The default asyncio executor caps at ~32 threads;
# sizing for the large-topology case (2500+ links → thousands of NIO add
# calls across hundreds of nodes).
_ubridge_sync_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=500,
    thread_name_prefix="ubridge-sync",
)


class UBridgeHypervisor:

    """
    Creates a new connection to a uBridge hypervisor control channel.

    Two transports, selected by which argument is set:
      * ``socket_path`` -> AF_UNIX (``-U``), authenticated in-kernel via
        SO_PEERCRED (ubridge accepts only its own UID; the compute process that
        spawned it shares that UID). Recommended on Linux.
      * ``host``/``port`` -> TCP (``-H``), retained for backward compatibility.

    :param socket_path: path to the uBridge AF_UNIX control socket (None for TCP)
    :param host: TCP hostname/IP (None for AF_UNIX)
    :param port: TCP port
    :param timeout: timeout integer for how long to wait for a response to commands sent to the
        hypervisor (defaults to 30 seconds)
    """

    # Used to parse Ubridge response codes
    error_re = re.compile(r"""^2[0-9]{2}-""")
    success_re = re.compile(r"""^1[0-9]{2}\s{1}""")

    def __init__(self, socket_path=None, host=None, port=None, timeout=30.0):

        # Exactly one transport is active: socket_path (AF_UNIX) or host/port (TCP).
        self._socket_path = socket_path
        self._host = host
        self._port = port
        self._version = "N/A"
        self._timeout = timeout
        self._reader = None
        self._writer = None
        self._recv_buf = b""  # leftover bytes from last sync recv
        self._send_lock = threading.Lock()

    async def connect(self, timeout=10):
        """
        Connects to the hypervisor.
        """

        begin = time.time()
        connection_success = False
        last_exception = None
        while time.time() - begin < timeout:
            await asyncio.sleep(0.1)
            try:
                if self._socket_path:
                    self._reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
                else:
                    # connect to a local address by default if listening on all addresses
                    if self._host == "0.0.0.0":
                        host = "127.0.0.1"
                    elif self._host == "::":
                        host = "::1"
                    else:
                        host = self._host
                    self._reader, self._writer = await asyncio.open_connection(host, self._port)
            except OSError as e:
                last_exception = e
                continue
            connection_success = True
            break

        if not connection_success:
            raise UbridgeError(f"Couldn't connect to hypervisor on {self.endpoint} :{last_exception}")
        else:
            log.info(f"Connected to uBridge hypervisor on {self.endpoint} after {time.time() - begin:.4f} seconds")

        try:
            await asyncio.sleep(0.1)
            version = await self.send("hypervisor version")
            self._version = version[0].split("-", 1)[0]
        except IndexError:
            self._version = "Unknown"

    @property
    def version(self):
        """
        Returns uBridge version.

        :returns: version string
        """

        return self._version

    async def close(self):
        """
        Closes the connection to this hypervisor (but leave it running).
        """

        await self.send("hypervisor close")
        self._writer.close()
        self._reader, self._writer = None

    async def stop(self):
        """
        Stops this hypervisor (will no longer run).
        """

        try:
            # try to properly stop the hypervisor
            await self.send("hypervisor stop")
        except UbridgeError:
            pass
        try:
            if self._writer is not None:
                await self._writer.drain()
                self._writer.close()
        except OSError as e:
            log.debug(f"Stopping hypervisor {self.endpoint} {e}")
        self._reader = self._writer = None

    async def reset(self):
        """
        Resets this hypervisor (used to get an empty configuration).
        """

        await self.send("hypervisor reset")

    @property
    def endpoint(self):
        """
        Returns a human-readable control endpoint: the AF_UNIX socket path when
        using -U, or host:port when using -H. Used for logging and errors.

        :returns: endpoint (string)
        """

        if self._socket_path:
            return self._socket_path
        return f"{self._host}:{self._port}"

    @locking
    async def send(self, command):
        """
        Sends commands to this hypervisor.

        :param command: a uBridge hypervisor command

        :returns: results as a list
        """

        # uBridge responses are of the form:
        #   1xx yyyyyy\r\n
        #   1xx yyyyyy\r\n
        #   ...
        #   100-yyyy\r\n
        # or
        #   2xx-yyyy\r\n
        #
        # Where 1xx is a code from 100-199 for a success or 200-299 for an error
        # The result might be multiple lines and might be less than the buffer size
        # but still have more data. The only thing we know for sure is the last line
        # will begin with '100-' or a '2xx-' and end with '\r\n'

        if self._writer is None or self._reader is None:
            raise UbridgeError("Not connected")

        try:
            command = command.strip() + "\n"
            log.debug(f"sending {command}")
            self._writer.write(command.encode())
            await self._writer.drain()
        except OSError as e:
            raise UbridgeError(
                "Lost communication with {endpoint} when sending command '{command}': {error}, uBridge process running: {run}".format(
                    endpoint=self.endpoint, command=command, error=e, run=self.is_running()
                )
            )

        # Now retrieve the result
        data = []
        buf = ""
        retries = 0
        max_retries = 10
        while True:
            try:
                try:
                    chunk = await self._reader.read(1024)
                except asyncio.CancelledError:
                    # task has been canceled but continue to read
                    # any remaining data sent by the hypervisor
                    continue
                except ConnectionResetError as e:
                    # Sometimes WinError 64 (ERROR_NETNAME_DELETED) is returned here on Windows.
                    # These happen if connection reset is received before IOCP could complete
                    # a previous operation. Ignore and try again....
                    log.warning(f"Connection reset received while reading uBridge response: {e}")
                    continue
                if not chunk:
                    if retries > max_retries:
                        raise UbridgeError(
                            "No data returned from {endpoint} after sending command '{command}', uBridge process running: {run}".format(
                                endpoint=self.endpoint, command=command, run=self.is_running()
                            )
                        )
                    else:
                        retries += 1
                        await asyncio.sleep(0.5)
                        continue
                retries = 0
                buf += chunk.decode("utf-8")
            except OSError as e:
                raise UbridgeError(
                    "Lost communication with {endpoint} after sending command '{command}': {error}, uBridge process running: {run}".format(
                        endpoint=self.endpoint, command=command, error=e, run=self.is_running()
                    )
                )

            # If the buffer doesn't end in '\n' then we can't be done
            try:
                if buf[-1] != "\n":
                    continue
            except IndexError:
                raise UbridgeError(
                    "Could not communicate with {endpoint} after sending command '{command}', uBridge process running: {run}".format(
                        endpoint=self.endpoint, command=command, run=self.is_running()
                    )
                )

            data += buf.split("\r\n")
            if data[-1] == "":
                data.pop()
            buf = ""

            # Does it contain an error code?
            if self.error_re.search(data[-1]):
                raise UbridgeError(data[-1][4:])

            # Or does the last line begin with '100-'? Then we are done!
            if data[-1][:4] == "100-":
                data[-1] = data[-1][4:]
                if data[-1] == "OK":
                    data.pop()
                break

        # Remove success responses codes
        for index in range(len(data)):
            if self.success_re.search(data[index]):
                data[index] = data[index][4:]

        log.debug(f"returned result {data}")
        return data

    def send_batch_sync(self, commands):
        """
        Send multiple commands to uBridge using blocking socket I/O.  Designed
        to run inside ``loop.run_in_executor`` so that a single per-node batch
        doesn't bounce through the event loop between every command, and
        batches for *different* nodes run in parallel across the thread pool.

        :param commands: iterable of command strings
        :raises UbridgeError: if any command fails
        """
        if self._writer is None:
            raise UbridgeError("Not connected")
        transport = self._writer.transport
        if transport is None or transport.is_closing():
            raise UbridgeError("Transport closed")
        sock = transport.get_extra_info("socket")
        if sock is None:
            raise UbridgeError("No underlying socket for sync send_batch")

        # Serialise access to this hypervisor's socket — only one batch (sync
        # or async) talks to uBridge at a time.  The node-level async lock
        # (:func:`_ubridge_send`) is held for the entire executor call, so no
        # async ``send()`` can interleave.
        with self._send_lock:
            sock.setblocking(True)
            try:
                for command in commands:
                    cmd = (command.strip() + "\n").encode()
                    sock.sendall(cmd)

                    # Read until the terminating line (100-… or 2xx-…)
                    buf = self._recv_buf
                    while True:
                        try:
                            chunk = sock.recv(4096)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            raise UbridgeError(
                                f"uBridge closed connection during '{command}'"
                            )
                        buf += chunk
                        decoded = buf.decode("utf-8", errors="replace")
                        # Last complete line determines termination
                        tail = decoded.rsplit("\r\n", 1)[-1]
                        if tail and tail[0] in "12" and tail[1:3].isdigit() and len(tail) >= 4 and tail[3] == "-":
                            break

                    # Check for error codes (2xx-…)
                    last_line = decoded.strip().split("\r\n")[-1]
                    if self.error_re.match(last_line):
                        raise UbridgeError(last_line[4:])

                    # Keep any leftover bytes (after the trailing \r\n) for the
                    # next read in the batch
                    trailer_start = decoded.rfind("\r\n")
                    if trailer_start >= 0:
                        self._recv_buf = buf[trailer_start + 2:]
                    else:
                        self._recv_buf = b""
            finally:
                sock.setblocking(False)
