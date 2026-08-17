# XRd Console --More-- Pager Bug (PTY 24-row window ignores terminal length 0)

## Background

Copilot commands with long output consistently fail against XRd (IOS XRv 9000 container) nodes: `device_show_run` running `show ipv4 interface brief` always reports `netmiko_multiline (failed)` (even as a single command); short-output commands like `show ipv4 interface <iface>` and `show running-config interface <iface>` work fine.

Failure sequence (from the 2026-08-18 logs):

1. First failure: `ReadTimeout: Pattern not detected: 'RP/0/RP0/CPU0:ios\#'` — the command echo already matched, but the prompt never appeared within 60s
2. The copilot's single-retry reuses the same netmiko session (nornir caches it on the host object) with half-consumed output in the buffer → the second failure dies earlier in `command_echo_read` (`Pattern not detected: 'show\ ipv4\ interface\ brief'`) — a follow-on effect of the dirty session, not an independent fault

## Root Cause (confirmed by live testing)

1. The GNS3 XRd console reaches the container CLI via **docker exec**; Docker allocates the exec PTY with a default **24-row** window
2. The XR pager (at least for table-engine/TABLAST-style commands) reads the **PTY window size (TIOCGWINSZ)**, not the CLI-level `terminal length` setting
3. Evidence: `show terminal` reports `Length: 0 lines, Width: 511 columns` (so `terminal length 0` IS in effect), yet `show ipv4 interface brief` pages after **exactly 24 lines** (timestamp + blank line + header + 21 interface rows) with `--More--`
4. The device parks at `--More--` waiting for a keypress → the channel goes silent → netmiko ReadTimeout. Netmiko never presses space; it assumes paging is disabled

### Dead ends (do not retry)

- `show ipv4 interface brief | no-more` → `% Invalid input detected`. `| no-more` is a **Junos** pipe modifier; IOS-XR does not have it (the CRS 4.1 doc covers filtering *at* the --More-- prompt, not this)
- `terminal length 0`: takes effect at the CLI layer, but this pager ignores it
- The XR CLI has no command to change PTY rows, and from inside the container you cannot reach another session's PTY

### Container-layer levers (deferred, high risk)

Changing the console plumbing in `gns3server/compute/docker/`: run the exec with `tty=false` (over pipes the CLI would likely fall back to the configured length = 0, i.e. no paging), or resize the exec PTY via the Docker API after creation. Both are XRd special-cases on a shared path used by every docker node's console — not accepted.

## Decision/Implementation (design agreed, NOT yet implemented)

Planned branch `feat/copilot-xrd-more-handling` (based on `feat/copilot-node-default-credentials`), three parts:

### 1. Display tool: for `cisco_xr*` platforms, replace netmiko_multiline with a channel-level loop that answers `--More--`

```python
conn.write_channel(cmd + "\n")
buf = ""
while not past_deadline:
    buf += conn.read_channel()
    if prompt_re.search(buf):            # normal path: prompt only
        break
    if re.search(r"--More--\s*$", buf):  # tail-anchored check only
        if still_quiet_after_one_poll:    # ~150ms quiet double-check
            conn.write_channel(" ")       # page forward (space, not q — q truncates)
    sleep(0.2)
```

**Key design constraint (user-flagged)**: never put `--More--` into netmiko's `expect_string` — expect does a `re.search` over accumulated output, so banner/description text containing "More" would false-trigger. The fundamental distinction used: **the real pager's `--More--` is the last byte of the stream (no newline, nothing follows until a keypress); a literal `--More--` in content is always followed by more arriving bytes**. Three safeguards: normal path never looks for More + tail anchoring + quiet double-confirmation.

### 2. Reconnect before retrying

The retry branch of `_run_all_device_configs_with_single_retry` must call `task.host.close_connection("netmiko")` first — otherwise the retry is doomed by the dirty session (the second error in the logs proves it). The config tool has the same retry structure and needs the same fix.

### 3. Persist session_log

Add `session_log_file` to `connection_options.netmiko.extras` in hosts_data (a native netmiko ConnectHandler parameter, passed through by nornir_netmiko). The copilot currently has no session_log anywhere, which made this bug a guessing game.

## Rationale

- Device side is unsolvable (CLI layer) or high-risk (docker plumbing layer); answering `--More--` at the copilot layer is a generic fix with zero plumbing risk: whatever the PTY row count, whatever odd console produces a pager, sending space on More heals it
- Tail-anchored detection drives the false-positive probability down to "content happens to be TCP-segment-split right after `--More--` AND stays silent" — which the quiet double-check then covers

## Related Files

- `gns3server/agent/gns3_copilot/tools_v2/display_tools_nornir.py:284-341` — `_run_all_device_configs_with_single_retry` (the dirty-session retry and the loop replacement point)
- `gns3server/agent/gns3_copilot/tools_v2/config_tools_nornir.py` — same retry structure to fix
- `gns3server/agent/gns3_copilot/utils/get_gns3_device_port.py` — hosts_data construction (session_log extras insertion point)
- `venv/.../netmiko/cisco/cisco_xr.py:14-22` — `session_preparation` (sends `terminal width 511` + `disable_paging`, both ineffective against this bug; note the comment at line 16: "IOS-XR has an issue where it echoes the command even though it hasn't returned the prompt")

## Examples

Live console transcript (2026-08-17):

```
RP/0/RP0/CPU0:ios#show terminal
Line "vty2", Location "", Type "VTY"
Length: 0 lines, Width: 511 columns      <- CLI-layer setting in effect

RP/0/RP0/CPU0:ios#show ipv4 interface brief | no-more
% Invalid input detected at '^' marker.  <- pipe does not exist on XR

RP/0/RP0/CPU0:ios#show ipv4 interface brief
(exactly 24 lines: timestamp + blank + header + 21 interface rows)
 --More--                                 <- the 24-row PTY is paging
```
