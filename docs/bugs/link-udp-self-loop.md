<!--
SPDX-License-Identifier: CC-BY-SA-4.0
See LICENSE file for licensing information.
-->

> This documentation is organized by AI with reference to actual code. AI can make mistakes — please verify against the source code when in doubt.


# Docker Link UDP Self-Loop Bug (One-Way Link)

## Bug Report

**Date**: 2026-08-14
**Severity**: Medium (one-way connectivity, CPU burn from packet duplication; intermittent)
**Status**: Open — root cause not yet pinpointed; workaround reliable
**Component**: Link wiring — `gns3server/controller/udp_link.py` (`_prepare` /
`pop_preallocated_udp_port` in `gns3server/controller/project.py`) interacting with
node/uBridge restarts

## Symptoms

Two Docker nodes (observed with Cisco XRd; likely node-type agnostic) linked on the
same compute cannot ping each other. Packet capture on the link shows only **one**
side sending ARP. The other side's traffic never appears on the link at all.

## Evidence (from a live occurrence)

Captured simultaneously on both nodes' uBridge `bridge1` (see diagnostics below):

| Direction | Result |
|---|---|
| A → B | works — A's ARP requests arrive at B's bridge, B replies |
| B → A | dead — B's replies/ICMP appear only on **B's own bridge** (duplicated ×2–×3), nothing arrives at A |
| B's `ethN` counters | RX ≈ TX ≈ 5000+ — B receives its own transmissions back |
| A's `ethN` counters | TX > 0, RX = 0 — never receives anything |

Conclusion: **B's `nio_udp` rport pointed at B's own lport** — a UDP self-loop. B's
uBridge had restarted ~77 s after A's (node stop/start during the session),
i.e. the link was re-wired at least once.

Deleting and re-creating the link fixed it immediately (fresh port allocation).

## Root Cause Analysis (narrowed, not final)

- `UDPLink._prepare()` builds mirrored NIO data correctly
  (`node1: lport=P1/rport=P2`, `node2: lport=P2/rport=P1`) — the logic itself is sound.
- Suspects for the corrupted runtime state:
  1. `Project.pop_preallocated_udp_port()` — the batch project-open preallocation
     (link-create performance work) racing with link re-creation;
  2. link re-creation racing a node/uBridge restart (commit NIOs to a uBridge that is
     being torn down/rebuilt), leaving a stale/self-pointing NIO on one side.
- A deterministic reproduction is still needed: create two Docker nodes + link,
  restart one node, then verify the UDP wiring (see diagnostics).

## Diagnostics (uBridge console is the fast path)

1. **uBridge console** — each node's uBridge listens on a Unix socket
   `/run/user/1000/gns3/ubridge-<node_id>.sock`; connect and send:
   `bridge list` (NIO count per bridge), and
   `bridge start_capture bridge<N> "/tmp/ub-<node>.pcap"` /
   `bridge stop_capture bridge<N>` to capture what the bridge actually forwards.
   Comparing the two ends' pcaps localizes the break immediately.
2. **Container counters** — `docker exec <cid> ip -s link show ethN`:
   TX>0/RX=0 → peer never returns; RX≈TX huge with µs-scale duplicates → self-loop.
3. **UDP sockets** — `ss -uln` (no `-p`; uBridge runs setuid-root so process names
   are hidden, ports are visible): a two-node link owns exactly two "orphan" UDP ports.
4. **Workaround** — delete and re-create the link (or stop/start both nodes).

Note: a Docker node's in-container `ethN` is a **TAP device** whose file descriptor
lives inside uBridge (the interface is created host-side, then moved into the
container namespace and renamed). There is no veth host end to look for — do not
waste time hunting for one in the host namespace.

## Related

- `docs/features/vendor-nos-xrd.md` — troubleshooting table entry pointing here.
- Link-create batch optimization (`controller/udp_link.py`, project-open bulk path).
