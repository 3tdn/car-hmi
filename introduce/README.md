# CAN-HMI — Hệ thống giám sát và điều khiển tín hiệu CAN Bus cho CarPC

> Tài liệu giới thiệu nội bộ — dành cho developer / lead review  
> Cập nhật: 2026-03-22 | Phiên bản: 0.8.0

---

## 1. Tổng quan

**CAN-HMI** là một phần mềm backend + frontend chạy trên **CarPC** (máy tính nhúng trên xe), có nhiệm vụ:

- **Đọc** tín hiệu thời gian thực từ các ECU xe qua **CAN Bus** (giao thức CAN 2.0B)
- **Giải mã** frame CAN thành các giá trị tín hiệu vật lý theo file DBC (v.d. `VehicleSpeed`, `EngineRPM`, `BrakePressure`)
- **Xử lý**: làm mượt tín hiệu, giới hạn tốc độ cập nhật, tính toán tín hiệu phái sinh, phát cảnh báo khi vượt ngưỡng
- **Lưu trữ** chuỗi thời gian vào SQLite, hỗ trợ truy vấn lịch sử
- **Phục vụ** REST API + WebSocket (FastAPI) để frontend web hiển thị dashboard real-time
- **Ghi ngược** tín hiệu trở lại CAN Bus khi người dùng thay đổi thông số từ giao diện

Hệ thống được thiết kế để chạy **không cần phần cứng thực** nhờ có **CAN Simulator** tích hợp sẵn.

---

## 2. Mục tiêu kỹ thuật

| Mục tiêu | Giá trị |
|---|---|
| Ngôn ngữ | Python ≥ 3.10 |
| Độ trễ đọc → WebSocket | ≤ 50 ms |
| Tốc độ xử lý | ≥ 1 000 signal updates/giây |
| Kích thước queue tối đa | 10 000 frame |
| Lưu trữ | SQLite, mặc định giữ 30 ngày |
| Triển khai | systemd service (`can-hmi.service`) hoặc Docker |

---

## 3. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────┐
│                       CarPC (Embedded)                      │
│                                                             │
│  [CAN Bus / Simulator] ──► [CAN Reader]                    │
│                                  │ asyncio.Queue            │
│                                  ▼                          │
│                          [Signal Pipeline]                   │
│                    Smooth → RateLimit → Computed → Alarm    │
│                                  │                          │
│               ┌──────────────────┼──────────────────┐       │
│               ▼                  ▼                   ▼       │
│       [Signal Store]        [SQLite DB]       [Alarm Log]   │
│       (in-memory)           (time-series)     (alarm_log)   │
│               │                  │                          │
│               └────────┬─────────┘                          │
│                        ▼                                    │
│               [FastAPI Server :8000]                        │
│           REST /signals  /alarms  /config                   │
│           WebSocket /ws/subscribe                           │
│                        │                                    │
└────────────────────────┼────────────────────────────────────┘
                         │ HTTP / WebSocket
                    [Web Dashboard]
                  (HTML + CSS + JS)
```

---

## 4. Các module chính

| Module | Thư mục | Mô tả |
|---|---|---|
| **CAN I/O** | `src/can_io/` | Đọc/ghi CAN frame, giải mã DBC/A2L/JSON |
| **Signal Processor** | `src/processor/` | Pipeline xử lý tín hiệu (4 stage) |
| **Signal Store** | `src/core/signal_store.py` | Cache in-memory, Observer pattern |
| **Storage** | `src/storage/` | SQLite repository, export CSV/JSON |
| **FastAPI Backend** | `src/api/` | REST routes, WebSocket, auth |
| **CAN Simulator** | `src/can_simulator/` | Giả lập ECU, hỗ trợ scenario YAML |
| **Config Manager** | `src/core/config_manager.py` | CRUD cấu hình YAML runtime |
| **Runner** | `src/core/runner.py` | Orchestrator khởi động toàn bộ hệ thống |

---

## 5. Tài liệu chi tiết

| File | Nội dung |
|---|---|
| [01_architecture.md](01_architecture.md) | Kiến trúc module, design patterns, sơ đồ |
| [02_api_reference.md](02_api_reference.md) | Toàn bộ REST endpoints + WebSocket protocol |
| [03_tech_stack.md](03_tech_stack.md) | Công nghệ, thư viện, môi trường |
| [04_signal_pipeline.md](04_signal_pipeline.md) | Luồng dữ liệu từ CAN Bus → Dashboard |

---

## 6. Quick Start (dev mode)

```bash
# 1. Cài đặt
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

# 2. Chạy ứng dụng (simulator bật sẵn)
can-hmi --config config/system.json

# 3. Mở dashboard
# http://localhost:8000

# 4. Chạy tests
pytest
```

---

## 7. Trạng thái phát triển  

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 1 | Foundation (config, project structure, all files) | ✅ DONE |
| 2 | CAN Reader (python-can + cantools, async producer) | ✅ DONE |
| 3 | Signal Processor (pipeline, filters, alarms, computed) | ✅ DONE |
| 4 | FastAPI full implementation (REST + WebSocket) | ✅ DONE |
| 5 | CLI / Runner (orchestrate full stack) | ✅ DONE |
| 6 | Frontend (Dev mode + User mode, whitelist) | ✅ DONE |
| 7 | Tests (unit + integration) | 🔄 In progress |
| 8 | Per-signal/channel WS subscribe, metrics push | ✅ DONE |

---

## 8. Liên hệ & Ghi chú

- Tất cả sơ đồ PlantUML nằm trong `diagram/` (C4 Level 1–2, Component, Class, ER, Sequence, Activity, Deployment)
- Tài liệu yêu cầu đầy đủ: `docs/requirement.md`
- Cấu hình runtime: `config/system.json`, `config/alarms.json`, `config/signals.json`
