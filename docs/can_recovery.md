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
