# Signal Metadata Without a Database

Signal values and DBC metadata do not use SQLite or another signal feature store. They exist only
in process memory. The adaptive-restraint crash dataset is a separate API data source and is not
part of this signal metadata lifecycle.

## Source of truth

- `AppRunner` loads each configured `can[].can_db_file` once with `DatabaseLoader`.
- The same loaded definitions used by CAN readers and writers build one
  `SignalMetadataCatalog` before the API starts.
- The catalog exposes unit, minimum, maximum, enum states, inferred tags, DBC source, and whether
  the message is transmitted by the local `CAR_PC` node.
- `/config`, `/config/signal/{signal_name}`, `/signals/available`, and the Dev Mode catalog read
  this same in-memory snapshot.
- Signal metadata APIs are read-only. There is no PATCH endpoint and no frontend override.

## DBC change lifecycle

DBC path changes are reboot-level configuration. Replacing a DBC file at the same path also
requires restarting the process. At startup the catalog is replaced as a whole, so removed
signals do not survive and changed metadata cannot be mixed with the previous DBC version.

Decoder, writer, and metadata therefore switch together on the same restart. Live DBC file
watching is intentionally not supported.

## Existing database files

Older `data/config.db`, `data/signals.db`, and SQLite sidecar files are no longer opened or
migrated. They can be deleted manually after the service is stopped. Keep a backup first only if
the old signal configuration records may still be needed for audit or reference.

Example cleanup from the repository root after stopping the service:

```bash
rm -f data/config.db data/config.db-shm data/config.db-wal
rm -f data/signals.db data/signals.db-shm data/signals.db-wal
```

This is an operator action, not an application migration. Leaving the old files in place has no
runtime effect and they will not grow because the application no longer opens them.
