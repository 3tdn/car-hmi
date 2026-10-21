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
| 20 | `20_sequence_restraints.puml` | Sequence | Restraints matching and video file delivery |
| 21 | `21_sequence_audio_demo.puml` | Sequence | HMI SEAL request → Panther sound selection → CAR-PC playback, periodic feedback, interruption/queueing, and completion |
| 22 | `22_state_audio_playback.puml` | State Machine | Audio request edges, type 1 interruption, type 2 FIFO queue, active-track feedback, and idle/error states |

## SEAL audio demo

The [audio demo guide](../docs/audio_demo.md) defines `HMI_SEAL_ActivationRequest`,
`SEAL_WithSound`, `HMI_Soundfile`, and CAR-PC feedback. In diagrams 21 and 22, **A** is
`audio.files`, **T** is `audio.position_update_interval_ms`, and playback type is selected
by `audio.track_queue_enable` (`false`: interrupt, `true`: queue). The component and data
flow diagrams also show the local player and the audio feedback path.

## SAD and SDD documentation

| Level | Documentation | Audio sequence | Lifecycle diagrams |
|---|---|---|---|
| Software Architecture Description (SAD) | [SAD overview and audio contract](SAD/README.md) | [Web HMI, CAR-PC, and PANTHER](SAD/SAD_sequence_audio_demo.puml) | [Startup](SAD/SAD_sequence_boot_startup.puml), [shutdown](SAD/SAD_sequence_boot_shutdown.puml) |
| Software Design Description (SDD) | [SDD audio components and behavior](SDD/README.md) | [Request handling and feedback](SDD/SDD_sequence_audio_demo.puml) | [Startup](SDD/SDD_sequence_boot_startup.puml), [shutdown](SDD/SDD_sequence_boot_shutdown.puml) |

SAD describes system responsibilities and signal exchanges. SDD describes request edges,
player/queue state, periodic feedback, completion, live settings, and runtime cleanup.

## Rendering

```bash
# Using PlantUML CLI
java -jar plantuml.jar diagram/*.puml

# Include architecture and design sequences
java -jar plantuml.jar diagram/SAD/*.puml diagram/SDD/*.puml

# Or VS Code extension: "PlantUML" by jebbs
# Ctrl+Shift+P → PlantUML: Preview Current Diagram
```

## Current metadata model

The realtime signal path has no SQL storage. `AppRunner` loads every configured DBC once,
replaces the process-local `SignalMetadataCatalog`, and passes that catalog to the API. Replacing
a DBC file requires a process restart so decoder, writer, and metadata all change together.
