# CAN-HMI Current Architecture

Verified against the source on 2026-09-26. This document describes implemented runtime behavior.
See [API Reference](api_reference.md), [Frontend Integration](frontend_integration.md), and
[Signal Metadata Without a Database](signal_metadata.md) for the detailed contracts.

## Runtime model

CAN-HMI is a single Python process coordinated by `AppRunner`:

```text
configured DBC files
        |
        +--> DatabaseLoader per CAN channel
                 |              |
                 |              +--> CANReader / CANWriter
                 |
                 +--> SignalMetadataCatalog (read-only, in memory)

CAN frame --> CANReader --> bounded asyncio.Queue --> SignalPipeline
                                                    |
                                                    +--> SignalStore
                                                             |
                                                             +--> REST / WebSocket
```

The realtime signal path has no SQL database or signal feature store. Realtime values and DBC
metadata are held only in memory. The adaptive-restraint route has a separate crash-dataset
SQLite/NumPy cache; it is not used for CAN signal values or metadata.

## Startup

1. Load and validate `config/system.json` as `AppConfig`.
2. Reserve the API socket so a duplicate instance fails before opening CAN resources.
3. Load every `can[].can_db_file` into one `DatabaseLoader` per channel.
4. Seed `SignalStore` with the known signals, units, and valid initial values.
5. Replace the entire `SignalMetadataCatalog` from those same loaders.
6. Create the CAN buses, readers, writers, router, processing pipeline, optional simulator, API,
   and watchdog.
7. Monitor critical long-running tasks. If one exits or raises, shut down the process so the
   service supervisor can restart it.

## DBC metadata lifecycle

`SignalMetadataCatalog` contains:

- signal name and source DBC;
- unit, minimum, maximum, description, and enum states;
- tags inferred from the signal name;
- whether the containing message is transmitted by the local `CAR_PC` node.

Duplicate signal names follow `CANWriterRouter` ownership: the first configured channel wins.
Catalog access returns copies so API code cannot mutate the runtime snapshot.

`can.*.can_db_file` is reboot-level configuration. Replacing a DBC at the same path also requires
a process restart. The new process rebuilds decoder, writer, and metadata together, and signals
removed from the DBC disappear from the catalog.

## Realtime data path

`CANReader` receives frames on its worker thread, decodes with its channel loader, and forwards
decoded frames to the event loop safely. `SignalPipeline` applies rate limiting and computed
signal stages before updating `SignalStore`. The API and WebSocket broadcaster read the latest
in-memory values. Signal history is not recorded.

## CAN write path

REST and Dev Mode write requests flow through profile/Dev Mode checks to `CANWriterRouter`.
The router selects the writer for the first channel that owns the signal. `CANWriter` validates
message TX ownership from the active DBC, applies the configured unwritten-sibling policy, encodes
the frame, and sends it through `python-can`.

The `writable` value returned by metadata APIs is derived from this DBC TX ownership. It is not a
frontend-editable permission.

## API ownership

- `/signals` and `/signals/{name}` expose current values.
- `/signals/available` exposes current values plus the shared DBC metadata snapshot.
- `/config` and `/config/signal/{name}` expose read-only signal metadata.
- `/config/system` manages supported system configuration fields and backups.
- `/system/health` and `/system/ready` report CAN reader state; no database health dimension exists.
- `/ws/subscribe` streams selected signals and metrics.

There is no PATCH route for per-signal metadata. Frontend display-specific choices must remain
client-side unless a separate explicit configuration contract is introduced later.

## Shutdown

Shutdown closes WebSockets and the API server, cancels runtime tasks, stops readers/simulator,
and closes CAN buses and the reserved socket. There is no persistence flush or database close.

## Operational consequences

- No signal-database lock, retention, migration, or unbounded history-growth risk.
- Restarting clears all latest signal values.
- Metadata changes become visible only after the DBC and process are restarted together.
- Old database files are ignored and may be deleted manually while the service is stopped.
