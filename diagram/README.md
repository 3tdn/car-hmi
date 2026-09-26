# CAN-HMI System — Diagram Index

All architecture and design diagrams for the CAN-HMI (CarPC) project.
Format: **PlantUML** (`.puml`).

## Diagrams

| # | File | Type | Description |
|---|---|---|---|
| 01 | `01_system_context.puml` | C4 Level 1 | System context — actors and external systems |
| 02 | `02_container.puml` | C4 Level 2 | Container diagram — major runtime blocks |
| 03 | `03_component.puml` | Component | CarPC internal modules and their relationships |
| 04 | `04_class_diagram.puml` | Class | Key runtime classes and in-memory state/metadata catalogs |
| 05 | `05_sequence_signal_read.puml` | Sequence | CAN Bus → Reader → Processor → in-memory store → Dashboard |
| 06 | `06_sequence_signal_write.puml` | Sequence | Dashboard → API → CAN Writer → CAN Bus (202 response, bus error handling) |
| 07 | `07_sequence_websocket.puml` | Sequence | WebSocket lifecycle (topic-based: /ws/signals, /ws/alarms, /ws/all; per-signal: /ws/subscribe) |
| 08 | `08_activity_pipeline.puml` | Activity | Realtime pipeline (rate limit + computed signals + backpressure) |
| 09 | `09_state_vehicle.puml` | State Machine | Vehicle state detection — **PROPOSED, not yet implemented** |
| 10 | `10_deployment.puml` | Deployment | Physical nodes, multi-channel CAN bus (vcan0/vcan1), LAN, frontend, deploy_linux.sh |
| 12 | `12_data_flow.puml` | Data Flow | End-to-end data flow overview |
| 13 | `13_sequence_startup_shutdown.puml` | Sequence | System startup (DBC metadata, pipeline, readers, API, watchdog) and graceful shutdown |
| 14 | `14_state_ws_client.puml` | State Machine | WebSocket client reconnection logic |
| 15 | `15_error_taxonomy.puml` | Error Taxonomy | Error classification, severity, and recovery strategies; monitoring via psutil /system/metrics |
| 16 | `16_sequence_signal_read.puml` | Sequence | Simplified in-memory signal read: CAN Bus → Queue → Pipeline → Store → WS |
| 17 | `17_sequence_signal_write.puml` | Sequence | Signal write-back: Frontend → API → CANWriterRouter → CAN Bus → ECU |
| 18 | `18_sequence_alarm_flow.puml` | Sequence | Proposed alarm flow; not implemented in the current runtime |
| 19 | `19_sequence_subscribe_flow.puml` | Sequence | Per-signal subscribe flow (/ws/subscribe) |

## Rendering

```bash
# Using PlantUML CLI
java -jar plantuml.jar Diagram/*.puml

# Or VS Code extension: "PlantUML" by jebbs
# Ctrl+Shift+P → PlantUML: Preview Current Diagram
```

## Current metadata model

The realtime signal path has no SQL storage. `AppRunner` loads every configured DBC once,
replaces the process-local `SignalMetadataCatalog`, and passes that catalog to the API. Replacing
a DBC file requires a process restart so decoder, writer, and metadata all change together.
