# Config Reload Sequence

```text
Client
  -> Config API
  -> Validate payload
  -> Create automatic backup
  -> Atomic write target file
  -> Acquire runner reload lock
  -> Apply hot-reloadable runtime sections
  -> Mark non-hot sections as restart-required
  -> Log reload success/failure
  -> Return structured reload status
```

Hot reload currently covers:
- processor queue policy
- processor queue size migration
- pipeline batch settings
- websocket update mode
- alarms threshold reload

Restart-required sections currently include:
- CAN topology/interface changes
- API bind host/port
- storage backend/path changes
- simulator/camera/writer/logging reinitialization
