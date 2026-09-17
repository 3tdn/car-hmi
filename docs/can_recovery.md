# CAN Recovery and Latency Runbook

## Runtime behavior

The CAN reader keeps socket receive work in a dedicated thread. It filters,
deduplicates, rate-gates, and decodes received frames, then coalesces the latest
changed values per CAN ID. At most one ingress callback is pending on the
asyncio event loop, so continuously changing frames cannot create an unbounded
callback backlog.

The signal pipeline processes at most `processor.batch_drain_size` frames per
cycle and keeps the newest value for each signal within that batch.

If a CAN bus is silent from startup or after receiving traffic for
`reader.stale_threshold_sec` (default `30`), the reader closes the bus and
starts reconnecting. Opening a replacement socket does not reset the backoff;
only the first frame received from it confirms recovery. Retry intervals are:

1. Five fast attempts: `1s`, `2s`, `4s`, `8s`, `16s`.
2. Ten attempts every `30s`.
3. Ten attempts every `1m`.
4. Ten attempts every `2m`, with intervals continuing to double until `1h`.
5. One retry every `1h` until the bus becomes available.

When a CAN reader is unavailable or stale, the runner sets the
`COM_Status_*Can` values defined by that reader's channel DBC to `0`; healthy
channels are left unchanged. Fresh CAN frames resume normal processing once a
reader reconnects.

Each CAN channel shares its bus between its reader and writer. When recovery
opens a replacement bus, the reader awaits the channel callback that switches
the paired writer and runner bus registry to that same replacement. CAN writes
therefore continue after a successful reconnect.

## Automatic SocketCAN channel selection

For a system with one physical CAN adapter, configure:

```json
{
  "can": [
    {
      "interface": "socketcan",
      "channel": "auto",
      "bitrate": 500000,
      "can_db_file": "db/can_db/Interface_Panther_To_CarPC_v9.dbc",
      "channel_tracking_signals": ["COM_Status_ElkCan"]
    }
  ]
}
```

At startup and on every reconnect, Car-HMI lists Linux CAN network interfaces
whose `IFF_UP` flag is set, opens them for probing, and selects the first one
that receives a CAN ID belonging to a message containing one of the configured
`channel_tracking_signals`. These names are resolved to message IDs from the already-loaded
channel DBC: the probe checks CAN message IDs, not signal values. Any configured tracking
message can confirm a candidate. With an empty list, all DBC messages with signals are
eligible. An unknown tracking signal is rejected when the runner resolves auto-selection message IDs at startup; verify names against live DBC metadata before saving.
The probe listens to all UP candidates in natural name order (`can0`, `can1`,
`can2`, ...), so a silent old interface cannot hide a working adapter whose
kernel name changed after USB reconnect. Non-matching traffic does not confirm
reader health; if matching DBC traffic stops for `reader.stale_threshold_sec`,
the existing reconnect loop closes the bus and runs auto-selection again. The
frame that validates a candidate is preserved and delivered to the reader, so
auto-selection does not discard a one-shot matching message. Probe filters are cleared after
selection, so `channel_tracking_signals` does not restrict normal signal reading.

If no matching channel is available at startup, Car-HMI continues in degraded
mode: the HTTP/WebSocket API remains available, readiness reports the CAN reader
as unavailable, CAN writes are rejected, and discovery continues in the
background. HTTP requests other than health/readiness probes, WebSocket
connections, and WebSocket client messages (including heartbeat pings) wake a
sleeping reconnect delay. These wakeups apply only to `channel: auto` and are
coalesced to at most one every five seconds so active frontends cannot create a
CAN retry storm. The normal staged reconnect backoff is otherwise unchanged.

A SocketCAN interface must already be configured with the correct bitrate and
have its `IFF_UP` flag set by the operating system. Automatic selection chooses
an interface name; it does not configure or bring a new interface UP.

`channel: "auto"` is intentionally supported only when `interface` is
`socketcan` and the configuration contains exactly one CAN channel. The DBC
must contain at least one message with a signal, and matching CAN traffic must
be present during the three-second selection probe.

## Dev Mode actions

Only authenticated Dev Mode requests can initiate recovery actions. System
controls are disabled when the configured API key is empty or a known
placeholder such as `change-me-in-production`. Configure a real key, then send
both:

```http
X-API-Key: <configured-key>
X-Dev-Mode: true
```

Start a reconnect attempt immediately:

```bash
curl -X POST http://<car-pc>:8000/system/can/retry \
  -H 'X-API-Key: <configured-key>' \
  -H 'X-Dev-Mode: true'
```

Gracefully restart Car-HMI:

```bash
curl -X POST http://<car-pc>:8000/system/reboot \
  -H 'X-API-Key: <configured-key>' \
  -H 'X-Dev-Mode: true'
```

For a systemd deployment, the reboot endpoint stops the process and relies on
`Restart=on-failure` in `can-hmi.service` to start it again after five seconds.
When started with `scripts/run_linux.sh`, the runner exits with code `75` and
the script restarts it after one second. Other nonzero exit codes still stop
the script so unexpected failures remain visible.

## Verification

Use these endpoints while diagnosing a disconnect:

```bash
curl http://<car-pc>:8000/system/health
curl http://<car-pc>:8000/system/ready
sudo journalctl -u can-hmi -f
```

The virtual-CAN callback regression can be run without hardware:

```bash
RUN_CAN_BACKLOG_TEST=1 .venv/bin/python -m pytest \
  tests/3_performance/test_virtual_can_callback_backlog.py -q -s
```

## Queue policy note

`queue_policy: drop_oldest` favors the most recent telemetry value under load.
It is appropriate for continuously updated values such as position or
temperature. Do not use it as the only delivery mechanism for one-shot safety
events or short pulses; those events need a durable queue, acknowledgement, or
ECU retransmission strategy.

## DBC reuse and frontend diagnostics

The runner loads each channel DBC and passes that loader to the channel components.
Automatic probing resolves tracking IDs from this loader; reconnecting does not parse the
DBC again for every candidate interface. The simulator has its own loader when enabled;
multiple configured channels may legitimately load the same file into separate loaders.
There is no claim of a process-wide DBC cache.

`No UP SocketCAN interface is available` is handled by the recovery loop, recorded in
backend logs/watchdog state, and does not terminate the application. Health/readiness
return HTTP 200 with JSON status/details; inspect that body for degraded/not-ready state.
They do not expose that exact internal `last_error` string as a dedicated diagnostic field.
See the [API reference](api_reference.md) and [frontend integration guide](frontend_integration.md).
