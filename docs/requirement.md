# CAN-HMI Implemented Requirements

| Field | Value |
|---|---|
| Document ID | REQ-CANHMI-001 |
| Version | 1.0.0 |
| Updated | 2026-09-26 |
| Status | Current implemented baseline |

This baseline replaces older draft requirements that described signal history, SQL signal
storage, alarm persistence, or frontend mutation of signal metadata. The independent adaptive
restraint crash dataset is outside the signal-storage contract.

## Functional requirements

### CAN definitions and metadata

- FR-DBC-1: Each configured CAN channel must load its own `can_db_file` before the API starts.
- FR-DBC-2: Decoder, writer, and signal metadata must use the same loaded DBC definition.
- FR-DBC-3: Signal metadata must be kept in process memory and must not require a database.
- FR-DBC-4: Rebuilding metadata must replace the complete previous catalog so deleted DBC signals
  cannot survive a reload.
- FR-DBC-5: DBC path/content changes require a process restart; live DBC watching is not supported.
- FR-DBC-6: For duplicate signal names, the first configured CAN channel owns metadata and writes.

### CAN receive and processing

- FR-RX-1: Blocking CAN receive runs outside the asyncio event-loop thread.
- FR-RX-2: Received frames are forwarded to a bounded asyncio queue using thread-safe scheduling.
- FR-RX-3: The reader must treat expected socket closure during stop/reconnect as normal shutdown,
  not as an unexpected negative-file-descriptor failure.
- FR-RX-4: The processing pipeline updates only the latest value in `SignalStore`.
- FR-RX-5: The application must not persist signal samples or expose signal-history APIs.

### CAN write

- FR-TX-1: Signal writes must be routed to the writer that owns the signal's CAN channel.
- FR-TX-2: The writer must reject messages whose active DBC sender does not permit local
  `CAR_PC` transmission.
- FR-TX-3: Profile restrictions apply to TX operations only; RX values and metadata remain readable.
- FR-TX-4: Batch writes forward only requested signals that pass the active profile and DBC checks.

### API and frontend

- FR-API-1: `/signals/available`, `/config`, `/config/signal/{name}`, and Dev Mode must use the
  shared in-memory metadata catalog rather than reparsing DBC files.
- FR-API-2: Signal metadata is read-only. The backend must not expose PATCH/POST/DELETE operations
  for per-signal metadata.
- FR-API-3: `/system/health`, `/system/ready`, and `/api/info` must not report database state.
- FR-API-4: Authentication and profile behavior follows the current OpenAPI/manual API contract.
- FR-API-5: A critical API task exit after startup must terminate the coordinated runtime so the
  external supervisor can restart the service.

### System configuration

- FR-CFG-1: `config/system.json` must not contain a storage or database path section.
- FR-CFG-2: `config/system.fields.json` must not advertise database settings.
- FR-CFG-3: DBC file selection is reboot-level; runtime-only settings may still use the documented
  live-apply policies.
- FR-CFG-4: Configuration backups are JSON files managed separately from signal data.

## Non-functional requirements

- NFR-1: Realtime reads must not wait for filesystem or database I/O.
- NFR-2: Metadata lookups must use the process-local snapshot and return defensive copies.
- NFR-3: No runtime component may create or grow `data/config.db` or `data/signals.db`.
- NFR-4: Startup must fail clearly for invalid config/DBC or an unavailable API bind address.
- NFR-5: Unexpected failure of a critical long-running task must not leave a partially alive process.
- NFR-6: Graceful shutdown must stop API, pipeline, readers, simulator, and CAN buses without a
  persistence flush step.

## Acceptance criteria

- AC-1: Starting the application with valid DBC files returns DBC unit/range/state/TX metadata
  through `/signals/available` and the read-only config endpoints.
- AC-2: Replacing catalog contents removes a signal that was deleted from a DBC.
- AC-3: `PATCH /config/signal/{name}` returns method not allowed.
- AC-4: The source and declared dependencies contain no signal SQLite repository/initializer or
  `aiosqlite` runtime dependency.
- AC-5: Health/readiness/info responses contain no `db_connected` or database readiness key.
- AC-6: Existing old database files are ignored; leaving them on disk does not change runtime
  behavior or cause them to grow.
- AC-7: Unit, functional, security, scenario, and opt-in runtime smoke suites pass.

## Operations

Old database files are not migrated or automatically deleted. Stop the service and follow
[Signal Metadata Without a Database](signal_metadata.md) for optional one-time manual cleanup.

The authoritative HTTP contract is [API Reference](api_reference.md) plus the generated
[OpenAPI snapshot](api.openapi.json). System setting policies are documented in
[System Configuration Management](system_config_management.md).
