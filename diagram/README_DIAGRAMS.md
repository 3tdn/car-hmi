# CarPC CAN-HMI - Complete Diagram Collection

This directory contains a comprehensive set of PlantUML diagrams documenting all aspects of the CarPC CAN-HMI system architecture, data flows, and processes.

## Diagram Index

### Core Architecture (Diagrams 01-04)
- **01_system_context.puml** - High-level system context and actors
- **02_container.puml** - Container-level architecture with major services
- **03_component.puml** - Component decomposition and interactions
- **04_class_diagram.puml** - Class structure and relationships

### Class Design Diagrams (21-25)
- **21_class_core.puml** - Core system classes (AppConfig, AppRunner, SignalStore, Signal, SystemMetrics)
- **22_class_can_io.puml** - CAN I/O layer classes (CANReader, CANWriter, DatabaseLoader, MessageDef, SignalDef)
- **23_class_processor.puml** - Signal processing pipeline classes (SignalPipeline, stages, AlarmChecker, Formula)
- **24_class_storage.puml** - Storage and database classes (SQLiteRepository, DataExporter, SignalLog, AlarmLog)
- **25_class_api.puml** - REST API and WebSocket classes (APIApp, Controllers, WebSocketManager)

### Signal Processing Flows (05-06, 26-27)
- **05_sequence_signal_read.puml** - Simplified signal read flow (CAN → Store)
- **26_sequence_signal_read_detailed.puml** - Detailed signal read with latency analysis
- **06_sequence_signal_write.puml** - Simplified signal write flow (API → CAN)
- **27_sequence_signal_write_detailed.puml** - Detailed signal write with encoding/routing

### WebSocket & Real-time (07, 28, 28)
- **07_sequence_websocket.puml** - Simplified WebSocket interaction
- **28_sequence_websocket.puml** - Detailed WebSocket subscribe and broadcast flow
- **14_state_ws_client.puml** - WebSocket client connection lifecycle (state machine)
- **31_state_websocket.puml** - Detailed WebSocket client state machine

### Alarm System (09, 18-19, 29-30)
- **09_state_vehicle.puml** - Vehicle state machine
- **18_sequence_alarm_flow.puml** - Simplified alarm flow
- **29_sequence_alarm_flow.puml** - Detailed alarm detection, state machine, and notifications
- **30_state_alarm.puml** - Alarm state machine with hysteresis and duration timers
- **19_sequence_subscribe_flow.puml** - Signal subscription flow

### Startup & Shutdown (13, sequence_*, 33)
- **13_sequence_startup_shutdown.puml** - Combined startup and shutdown sequence
- **sequence_startup.puml** - Detailed startup flow (separated)
- **sequence_shutdown.puml** - Detailed shutdown flow (separated)
- **33_activity_startup.puml** - Startup as activity diagram with timelines

### Data & Processing Flows (08, 12, 32, 34)
- **08_activity_pipeline.puml** - Simplified pipeline activity diagram
- **12_data_flow.puml** - System data flow overview
- **32_activity_pipeline.puml** - Detailed pipeline batch processing activity
- **34_data_flow.puml** - Complete system data flow with latency characteristics

### API & Integration (35-36)
- **35_api_endpoints.puml** - All REST API endpoints (24+ endpoints)
- **36_component_interaction.puml** - Component interactions and design patterns

### System Design (10-11, 37-38)
- **10_deployment.puml** - Deployment architecture
- **11_database_er.puml** - Database entity-relationship diagram
- **37_deployment.puml** - Detailed deployment with Docker, Kubernetes, and hardware
- **38_configuration.puml** - Configuration file structure (system.json, alarms.json, can.json)

### Advanced Topics (15, 16-17, 20, 40-43)
- **15_error_taxonomy.puml** - Error types and handling
- **16_sequence_signal_read.puml** - Signal read with restraints system
- **17_sequence_signal_write.puml** - Signal write with restraints system
- **20_sequence_restraints.puml** - Adaptive restraint system flow
- **40_error_handling.puml** - Error handling and recovery strategies
- **41_performance.puml** - Performance characteristics, optimization, and tuning
- **42_testing.puml** - Testing strategy (unit, integration, E2E, load tests)
- **43_security.puml** - Security architecture and authentication model

### Documentation (39)
- **39_documentation_map.puml** - Meta-diagram showing all diagrams and their relationships

### Camera Stream (44)
- **44_activity_camera_stream.puml** - Camera MJPEG stream proxy: single-upstream fan-out
  activity diagram covering `CameraStreamProxy` (open_subscription, `_run_upstream`,
  `_broadcast`, `_track_fps`, `stream_queue`, `_remove_subscriber`) and the
  `/api/camera/stream` + `/api/camera/status` routes.

## Quick Navigation

### For New Developers
Start here for understanding the system:
1. **01_system_context.puml** - Get overview
2. **02_container.puml** - Understand main components
3. **04_class_diagram.puml** - See class relationships
4. **21-25_class_*.puml** - Deep dive into each module

### For Understanding Data Flow
Trace how signals move through the system:
1. **05_sequence_signal_read.puml** - See the flow
2. **26_sequence_signal_read_detailed.puml** - Understand latencies
3. **34_data_flow.puml** - See system-wide data movement

### For API Integration
Building on the REST/WebSocket APIs:
1. **35_api_endpoints.puml** - See all endpoints
2. **36_component_interaction.puml** - Understand integration points
3. **25_class_api.puml** - See API class structure

### For System Configuration
Understanding configuration and deployment:
1. **38_configuration.puml** - Learn config files
2. **37_deployment.puml** - See deployment options
3. **43_security.puml** - Understand security model

### For Troubleshooting
Debugging system issues:
1. **26_sequence_signal_read_detailed.puml** - Trace signal reception
2. **27_sequence_signal_write_detailed.puml** - Trace signal writing
3. **29_sequence_alarm_flow.puml** - Debug alarm issues
4. **40_error_handling.puml** - Understand error paths

### For Performance Tuning
Optimizing system performance:
1. **41_performance.puml** - See performance characteristics
2. **32_activity_pipeline.puml** - Understand pipeline latency
3. **42_testing.puml** - See performance test strategies

### For Testing & QA
Testing the system:
1. **42_testing.puml** - See test strategy
2. **43_security.puml** - Understand security testing
3. **40_error_handling.puml** - See error scenarios

## Diagram Legend

**Colors & Symbols:**
- 🔵 **Blue** - Data/Information flows
- 🟢 **Green** - Successful operations
- 🟠 **Orange** - Warning/Alert states
- 🔴 **Red** - Error states
- **→** - Direct dependency/flow
- **-.->** - Optional/conditional flow
- **══** - Strong coupling
- **--** - Weak coupling

## Key Metrics & Facts

**Performance:**
- E2E Signal Latency: 12-15ms (95th percentile)
- Max Throughput: 10,000+ signals/sec
- Concurrent Connections: 100+ WebSocket clients
- Memory Usage: 150-250 MB typical
- CPU Usage: 10-15% at normal load

**Architecture:**
- 6 Main Modules: core, can_io, processor, storage, api, can_simulator
- 24+ REST API Endpoints
- 2 WebSocket Protocols (legacy + command-based)
- 3 Configuration Files
- 3 SQLite Tables

**Deployment:**
- Cold Start: ~5 seconds
- Scalability: Vertical & Horizontal
- Platforms: Linux, Windows (via WSL)
- Containerization: Docker supported
- Orchestration: Kubernetes compatible

## How to Render Diagrams

### Using PlantUML Online
1. Visit https://www.plantuml.com/plantuml/uml/
2. Copy content of any `.puml` file
3. Paste into editor to see rendered diagram

### Using PlantUML CLI
```bash
# Install PlantUML
sudo apt-get install plantuml

# Render single diagram
plantuml diagram/21_class_core.puml -o diagram/out

# Render all diagrams
plantuml diagram/*.puml -o diagram/out
```

### Using VS Code Extension
1. Install "PlantUML" extension (jebbs.plantuml)
2. Open any `.puml` file
3. Press `Alt+D` to preview

### Using Docker
```bash
docker run --rm -v $(pwd)/diagram:/data -v $(pwd)/diagram/out:/out \
  plantuml/plantuml -o /out *.puml
```

## Document Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-04 | Initial comprehensive diagram collection (43 diagrams) |

## Related Documentation

- **ARCHITECTURE_ANALYSIS.md** - Detailed written analysis of the system
- **README.md** - Project overview and getting started
- **introduce/** - Technical introduction documents
- **docs/** - System requirements and specifications

## Maintenance Notes

When updating diagrams:
1. Keep consistent naming convention: `NN_type_name.puml`
2. Update **39_documentation_map.puml** with new diagrams
3. Update this **README.md** with new diagram descriptions
4. Keep color schemes consistent across diagrams
5. Add performance/latency notes where relevant

## Support

For questions about specific diagrams:
1. Check the embedded notes in each diagram
2. Refer to related `.md` documentation
3. Review the source code in `src/` directory
4. Check test files in `tests/` for usage examples
