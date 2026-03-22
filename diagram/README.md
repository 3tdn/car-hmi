# CAN-HMI System — Diagram Index

All architecture and design diagrams for the CAN-HMI (CarPC) project.
Format: **PlantUML** (`.puml`).

## Diagrams

| # | File | Type | Description |
|---|---|---|---|
| 01 | `01_system_context.puml` | C4 Level 1 | System context — actors and external systems |
| 02 | `02_container.puml` | C4 Level 2 | Container diagram — major runtime blocks |
| 03 | `03_component.puml` | Component | CarPC internal modules and their relationships |
| 04 | `04_class_diagram.puml` | Class | Key classes, interfaces, design patterns (Strategy, Pipeline, Repository, Observer, Factory) |
| 05 | `05_sequence_signal_read.puml` | Sequence | CAN Bus → Reader → Processor → Dashboard (4 stages, with NFR timing) |
| 06 | `06_sequence_signal_write.puml` | Sequence | Dashboard → API → CAN Writer → CAN Bus (202 response, bus error handling) |
| 07 | `07_sequence_websocket.puml` | Sequence | WebSocket lifecycle (topic-based: /ws/signals, /ws/alarms, /ws/all) |
| 08 | `08_activity_pipeline.puml` | Activity | Signal processing pipeline (4 stages + backpressure + exception handling) |
| 09 | `09_state_vehicle.puml` | State Machine | Vehicle state detection — **PROPOSED, not yet implemented** |
| 10 | `10_deployment.puml` | Deployment | Physical nodes, CAN bus, LAN, frontend, dev simulator |
| 11 | `11_database_er.puml` | ER | Database schema (signal_log, signal_config with group_name/widget_type, alarm_log with triggered_at) |
| 12 | `12_data_flow.puml` | Data Flow | End-to-end data flow overview |
| 13 | `13_sequence_startup_shutdown.puml` | Sequence | System startup and graceful shutdown |
| 14 | `14_state_ws_client.puml` | State Machine | WebSocket client reconnection logic |
| 15 | `15_error_taxonomy.puml` | Error Taxonomy | Error classification, severity, and recovery strategies (req 2.9) |

## Rendering

```bash
# Using PlantUML CLI
java -jar plantuml.jar Diagram/*.puml

# Or VS Code extension: "PlantUML" by jebbs
# Ctrl+Shift+P → PlantUML: Preview Current Diagram
```

## Revision History

- **Initial**: Created 14 PlantUML diagrams from requirement spec
- **Rounds 1-5**: Fixed deployment/component/data-flow errors, added CANSimulator classes, VehicleStateMachine, per-signal WS, bus error handling
- **Rounds 6-10**: Added Alarm/SignalValue/SignalRecord/AppConfig/AlarmConfig data classes, package groupings, export path, backpressure, bidirectional REST
- **Rounds 11-15**: Fixed exception flow in activity, WS close frame, merged arrows, DBC/A2L/CANdb JSON alignment, concurrent heartbeat
- **Rounds 16-20**: Non-writable signal check, flush(), GStreamer path, cold start NFR timing, FK labels, README updates
- **Review Round 1**: Fix ER resolved_at, add WriterConfig/ShutdownConfig/SupervisorConfig to class diagram, alarm_log persist in read seq, /ready in startup, deployment systemd/Docker note, new #15 error taxonomy diagram
- **Review Round 2**: Add alarm ops to ISignalRepository, alarm ACK in data flow, ws/alarms in WS seq, shutdown rejection in write seq, alarm history route in component
- **Review Round 3**: Fix alarm_log index to (signal_name,timestamp), quote reserved word in ER, connect r_system to core_store and proc_pipe
- **Review Round 4**: Add /ready to container monitoring, alarm WS push in read seq (AC-13), APIKeyAuth class for security 2.11
- **Review Round 5**: Configurable queue_policy in activity diagram (block/drop_oldest/reject), align SQLiteRepository with ISignalRepository interface
- **Code Sync v0.7.0**: Full sync with actual codebase — removed VehicleStateMachine (not implemented), reordered pipeline stages to SmoothingFilter→RateLimiter→ComputedSignals→AlarmChecker, updated class diagram (all classes/fields match code), fixed ER schema (signal_log, alarm_log, signal_config match database.py), added bus_factory/parser/config_manager to component diagram, fixed WebSocket to topic-based (/ws/signals, /ws/alarms, /ws/all), fixed write endpoint to 202, removed video/camera from data flow, marked 09_state_vehicle as PROPOSED
