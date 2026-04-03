# Quy Trình Phát Triển Phần Mềm Chuyên Nghiệp Với AI Agent (GitHub Copilot)

| Field | Value |
|---|---|
| Tài liệu | Quy trình phát triển chuẩn với AI Agent |
| Ngày tạo | 2026-03-19 |
| Công cụ | GitHub Copilot (Agent Mode) |
| Ngôn ngữ | Tiếng Việt |

---

## Tổng quan

Tài liệu mô tả **quy trình phát triển phần mềm chuyên nghiệp** mà các công ty phần mềm hàng đầu áp dụng khi sử dụng AI Agent (GitHub Copilot). Quy trình được chia thành **8 giai đoạn chính**, mỗi giai đoạn có các bước cụ thể và đầu ra rõ ràng.

---

## Giai đoạn 1: Khởi tạo dự án (Project Initialization)

### Mục tiêu
Thiết lập nền tảng dự án, cấu trúc thư mục và môi trường phát triển.

### Thông tin dự án thực tế — CAN-HMI

| Trường | Giá trị |
|---|---|
| Tên dự án | `car-hmi` |
| Remote repository | `git@github.com:thithuongdk/car-hmi.git` |
| Ngày khởi tạo | 2026-03-18 (commit `088fc9c` — "add doc") |
| Branch chính | `main` |
| Branch làm việc | `dev_work` |
| Tổng số commit hiện tại | 35+ commits |
| Công cụ AI | GitHub Copilot (Agent Mode) — VS Code |
| Ngôn ngữ chính | Python 3.12 |

### Cấu trúc thư mục hiện tại

```
car-hmi/                        ← root
├── .git/                       ✅ Git repo đã khởi tạo
├── .vscode/
│   └── settings.json           ✅ VS Code settings
├── agent/                      ✅ Tài liệu AI Agent
│   ├── agent.log               ✅ Lịch sử commit của AI Agent
│   ├── prompt.md               ✅ Prompt chuẩn đã dùng
│   └── steps.md                ✅ Quy trình phát triển (file này)
├── data/                       ✅ Dữ liệu DBC/A2L
│   ├── can_db/
│   │   ├── Interface_Panther_To_CarPC_generated.dbc
│   │   ├── mercedes_common.dbc
│   │   └── vehicle.dbc
│   └── ecu_db/
│       └── mercedes_ecu.a2l
├── diagram/                    ✅ 15 PlantUML diagrams
│   ├── 01_system_context.puml
│   ├── ... (15 files)
│   └── README.md
├── docs/
│   └── requirement.md          ✅ Requirement spec v0.6.0
└── scripts/                    ✅ Scripts tiện ích
```

### Trạng thái các bước khởi tạo

| # | Bước | Mô tả | Trạng thái | Đầu ra thực tế |
|---|---|---|---|---|
| 1.1 | Tạo repository | Git repo + remote GitHub | ✅ Hoàn thành | `git@github.com:thithuongdk/car-hmi.git` |
| 1.2 | Branch strategy | Branch `main` + `dev_work` cho phát triển cô lập | ✅ Hoàn thành | Branch `dev_work` đang hoạt động |
| 1.3 | Thư mục dữ liệu | `data/can_db/` + `data/ecu_db/` với file DBC/A2L thực tế | ✅ Hoàn thành | 3 file `.dbc`, 1 file `.a2l` |
| 1.4 | Thư mục tài liệu | `Docs/`, `Diagram/`, `Agent/` | ✅ Hoàn thành | Tài liệu đầy đủ |
| 1.5 | VS Code config | `.vscode/settings.json` cho team | ✅ Hoàn thành | `.vscode/settings.json` |
| 1.6 | `.gitignore` | Loại trừ `__pycache__`, `.venv`, `*.db`, secrets | ✅ Xong | — |
| 1.7 | `pyproject.toml` | Metadata + dependencies theo `Docs/requirement.md` section 5 | ✅ Xong | — |
| 1.8 | Cấu trúc `src/` | Tạo skeleton theo cấu trúc trong requirement (can_io, processor, api, ...) | ✅ Xong | — |
| 1.9 | `config/` | `system.json`, `alarms.json`, `signals.json` theo spec section 6 | ✅ Xong | — |
| 1.10 | Coding standards | `ruff.toml`, `.editorconfig`, pre-commit hooks | ✅ Xong | — |
| 1.11 | CI/CD pipeline | `.github/workflows/ci.yml` (lint → test → coverage) | ⬜ Chưa tạo | Phase 6 |

### Các bước cần thực hiện tiếp theo

#### Bước 1.6 — Tạo `.gitignore`
```bash
# Prompt cho AI Agent:
Tạo file .gitignore cho dự án Python car-hmi. Bao gồm: __pycache__, .venv, 
*.pyc, *.db (SQLite), logs/, .env, secrets, .pytest_cache, dist/, build/.
```

#### Bước 1.7 — Tạo `pyproject.toml`
```bash
# Prompt cho AI Agent:
Tạo pyproject.toml cho dự án car-hmi Python 3.12. Dependencies theo requirement.md 
section 5: python-can>=4.4, cantools>=39.0, fastapi>=0.115, uvicorn[standard]>=0.30,
aiosqlite>=0.20, pydantic>=2.9, pyyaml>=6.0. Dev deps: pytest>=8.0, ruff>=0.5, 
pytest-asyncio>=0.24, httpx>=0.27, pytest-cov>=5.0, locust>=2.29.
```

#### Bước 1.8 — Tạo cấu trúc `src/`
```bash
# Prompt cho AI Agent:
Tạo cấu trúc thư mục src/ cho dự án car-hmi theo requirement.md section 3. 
Bao gồm: can_simulator/, can_io/, processor/, storage/, api/routes/, core/, 
frontend/. Mỗi package có __init__.py. Tạo skeleton file rỗng theo đúng tên file 
đã định nghĩa trong requirement (simulator.py, reader.py, writer.py, pipeline.py, ...).
```

#### Bước 1.9 — Tạo `config/` với file JSON mẫu
```bash
# Prompt cho AI Agent:
Tạo thư mục config/ với 3 file JSON theo requirement.md section 6:
- system.json: CAN config, simulator, api, storage, processor, writer, shutdown, supervisor, logging
- alarms.json: alarm thresholds cho VehicleSpeed, EngineRPM, CoolantTemp
- signals.json: display config cho các signal trong data/can_db/
```

### Prompt mẫu cho AI Agent — Khởi tạo toàn bộ
```
Dự án car-hmi đã có: .git, data/can_db/*.dbc, data/ecu_db/*.a2l, Docs/requirement.md, 
15 PlantUML diagrams trong Diagram/.

Cần tạo thêm:
1. .gitignore (Python + SQLite + secrets)  
2. pyproject.toml với dependencies từ requirement.md section 5
3. Skeleton src/ theo cấu trúc trong requirement.md section 3
4. config/system.json, config/alarms.json, config/signals.json theo section 6
5. ruff.toml với cấu hình linting chuẩn
6. .editorconfig (indent=4 spaces, UTF-8, LF)
```

### Branch strategy thực tế

```
main ──────────────────────────────────────────────► production-ready
         │
         └──► dev_work ──────────────────────────► đang phát triển (active)
                   │
                   └──► feature/can-reader         (sắp tạo)
                   └──► feature/signal-processor   (sắp tạo)
                   └──► feature/api                (sắp tạo)
```

---

## Giai đoạn 2: Phân tích yêu cầu (Requirement Analysis)

### Mục tiêu
Thu thập, phân tích và tài liệu hóa yêu cầu hệ thống một cách chi tiết.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 2.1 | Thu thập yêu cầu | Liệt kê tất cả yêu cầu chức năng và phi chức năng | Danh sách yêu cầu thô |
| 2.2 | Viết tài liệu yêu cầu | AI Agent hỗ trợ viết requirement spec đầy đủ, có cấu trúc | `requirement.md` |
| 2.3 | Định nghĩa tiêu chí chấp nhận | Xác định Acceptance Criteria (AC) cho từng feature | Bảng AC (AC-1, AC-2, ...) |
| 2.4 | Xác định NFR | Lập bảng Non-Functional Requirements: performance, security, scalability | Bảng NFR |
| 2.5 | Review yêu cầu (lặp) | AI Agent review requirement qua nhiều vòng, tìm lỗ hổng, mâu thuẫn | Requirement đã chỉnh sửa |
| 2.6 | Tạo glossary | Định nghĩa các thuật ngữ chuyên ngành trong dự án | Bảng glossary |

### Prompt mẫu cho AI Agent
```
Review tài liệu requirement.md, liệt kê 5 điểm mạnh, 5 điểm yếu và 3 lỗi. 
Sau đó áp dụng sửa chữa cụ thể. Lặp lại 5 vòng.
```

---

## Giai đoạn 3: Thiết kế kiến trúc (Architecture Design)

### Mục tiêu
Thiết kế kiến trúc hệ thống, tạo các biểu đồ mô tả cấu trúc và luồng dữ liệu.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 3.1 | Thiết kế tổng quan (C4 Model) | AI Agent tạo System Context (L1), Container (L2), Component (L3) | PlantUML diagrams |
| 3.2 | Thiết kế class diagram | Xác định các class chính, interface, design patterns | Class diagram (`.puml`) |
| 3.3 | Thiết kế sequence diagrams | Mô tả luồng xử lý chính: read, write, WebSocket, startup/shutdown | Sequence diagrams |
| 3.4 | Thiết kế database schema | ER diagram, schema SQL, indexes, retention policy | ER diagram + SQL |
| 3.5 | Thiết kế state machine | Mô hình trạng thái cho các entity phức tạp | State machine diagrams |
| 3.6 | Thiết kế data flow | Luồng dữ liệu end-to-end từ input → processing → output | Data flow diagram |
| 3.7 | Thiết kế deployment | Kiến trúc triển khai: nodes, networks, containers | Deployment diagram |
| 3.8 | Thiết kế error taxonomy | Phân loại lỗi, severity, recovery strategy | Error taxonomy diagram |
| 3.9 | Review kiến trúc (lặp) | AI Agent review diagrams so với requirement qua nhiều vòng | Diagrams đã chỉnh sửa |

### Prompt mẫu cho AI Agent
```
Tạo 14 PlantUML diagrams dựa trên requirement.md: system context, container, 
component, class, sequence (read/write/WS/startup), activity, state machine, 
deployment, ER, data flow. Sau đó review 5 vòng so với requirement.
```

---

## Giai đoạn 4: Triển khai mã nguồn (Implementation)

### Mục tiêu
Viết mã nguồn theo thiết kế, đảm bảo chất lượng code và tuân thủ coding standards.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 4.1 | Tạo skeleton code | AI Agent tạo cấu trúc module, interface, abstract class | Source files cơ bản |
| 4.2 | Implement core modules | Triển khai từng module theo thứ tự dependency (core → io → processor → api) | Source code |
| 4.3 | Implement data layer | Database connection, repository, migration | Storage module |
| 4.4 | Implement API layer | REST endpoints, WebSocket handlers, auth middleware | API module |
| 4.5 | Implement frontend | Dashboard UI, WebSocket client, widgets | Frontend files |
| 4.6 | Config & validation | Config loader, schema validation (Pydantic) | Config module |
| 4.7 | Error handling | Implement error taxonomy, logging, recovery strategies | Error handling code |
| 4.8 | Code review (AI) | AI Agent review code: security, performance, best practices | Review comments + fix |

### Prompt mẫu cho AI Agent
```
Implement module src/can_io/reader.py theo class diagram và sequence diagram. 
Sử dụng python-can + cantools, async reader với asyncio.Queue, 
hỗ trợ reconnect khi bus lỗi.
```

### Nguyên tắc khi dùng AI Agent viết code
- **Chia nhỏ task**: Mỗi prompt chỉ yêu cầu 1 module hoặc 1 feature cụ thể
- **Cung cấp context**: Đính kèm interface/class diagram để AI hiểu contract
- **Review từng bước**: Không merge code chưa review vào main branch
- **Test song song**: Viết test ngay sau khi implement xong module

---

## Giai đoạn 5: Kiểm thử (Testing)

### Mục tiêu
Đảm bảo chất lượng phần mềm thông qua các cấp độ kiểm thử.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 5.1 | Unit tests | AI Agent tạo test cho từng module riêng lẻ | `tests/test_*.py` |
| 5.2 | Integration tests | Test kết hợp giữa các module (API + DB, CAN + Processor) | Integration test files |
| 5.3 | E2E tests | Test toàn bộ luồng từ đầu đến cuối | E2E test scripts |
| 5.4 | Performance tests | Load testing, benchmark theo NFR (latency, throughput) | Performance report |
| 5.5 | Security tests | Kiểm tra OWASP Top 10, auth bypass, injection | Security report |
| 5.6 | Coverage check | Đảm bảo coverage ≥ 80% cho toàn bộ source code | Coverage report |
| 5.7 | Fix failing tests | AI Agent sửa code/test khi test fail | Code fix commits |

### Prompt mẫu cho AI Agent
```
Viết unit tests cho module src/processor/pipeline.py. Mock CAN bus bằng VirtualBus,
dùng in-memory SQLite. Coverage target ≥ 90%. Test cả happy path và error cases.
```

---

## Giai đoạn 6: CI/CD & Tự động hóa (Automation)

### Mục tiêu
Thiết lập pipeline tự động cho build, test, lint, deploy.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 6.1 | Thiết lập CI pipeline | GitHub Actions / GitLab CI: lint → test → coverage → build | `.github/workflows/ci.yml` |
| 6.2 | Thiết lập CD pipeline | Auto-deploy khi merge vào main (staging → production) | CD workflow |
| 6.3 | Docker build | Tạo Dockerfile, docker-compose cho dev và production | `Dockerfile`, `docker-compose.yml` |
| 6.4 | Pre-commit hooks | Tự động lint, format, type check trước khi commit | `.pre-commit-config.yaml` |
| 6.5 | Automated release | Semantic versioning, changelog generation, tag release | Release workflow |

### Prompt mẫu cho AI Agent
```
Tạo GitHub Actions CI pipeline: checkout → setup Python 3.12 → install deps → 
ruff check → pytest with coverage → upload coverage report. 
Trigger khi push hoặc PR vào main/dev.
```

---

## Giai đoạn 7: Review & Cải tiến (Iterative Improvement)

### Mục tiêu
Liên tục cải thiện chất lượng tài liệu, thiết kế và mã nguồn qua nhiều vòng review.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 7.1 | Review tài liệu (lặp) | AI Agent review requirement/design docs, tìm inconsistency | Docs đã cải thiện |
| 7.2 | Review diagrams (lặp) | So sánh diagrams với requirement, sửa lỗi, bổ sung thiếu | Diagrams đã cập nhật |
| 7.3 | Code refactoring | AI Agent đề xuất refactor: simplify, extract, rename | Refactored code |
| 7.4 | Dependency audit | Kiểm tra dependency outdated, vulnerability, license | Audit report |
| 7.5 | Performance tuning | Profiling, optimization dựa trên benchmark results | Optimized code |

### Quy trình review lặp (Iterative Review)
```
Quy trình mỗi vòng review:
1. Đọc toàn bộ tài liệu/code hiện tại
2. Liệt kê: 5 điểm mạnh, 5 điểm yếu, 3 lỗi cụ thể
3. Áp dụng sửa chữa cho các lỗi tìm được
4. Commit với message rõ ràng mô tả thay đổi
5. Lặp lại N vòng (thường 5–20 vòng tùy độ phức tạp)
```

### Prompt mẫu cho AI Agent
```
Review 5 vòng: so sánh diagrams với requirement.md. Mỗi vòng liệt kê 5 điểm mạnh, 
5 điểm yếu, 3 lỗi, áp dụng sửa chữa cụ thể, commit. 
Push sau mỗi 5 vòng.
```

---

## Giai đoạn 8: Triển khai & Vận hành (Deployment & Operations)

### Mục tiêu
Triển khai hệ thống lên môi trường production và giám sát hoạt động.

### Các bước thực hiện

| # | Bước | Mô tả | Đầu ra |
|---|---|---|---|
| 8.1 | Chuẩn bị deployment | Tạo systemd service, Docker image, config production | Deployment files |
| 8.2 | Deploy staging | Triển khai lên môi trường staging, smoke test | Staging environment |
| 8.3 | Deploy production | Triển khai lên production với rollback plan | Production environment |
| 8.4 | Monitoring setup | Thiết lập health check, logging, alerting, metrics | Monitoring dashboard |
| 8.5 | Documentation | Cập nhật README, API docs, deployment guide, runbook | Documentation files |
| 8.6 | Handover | Chuyển giao cho team vận hành, training | Handover docs |

### Prompt mẫu cho AI Agent
```
Tạo systemd service file cho ứng dụng Python, bao gồm: WatchdogSec, 
Restart=on-failure, environment variables, health check endpoint. 
Thêm Dockerfile với HEALTHCHECK.
```

---

## Tổng hợp quy trình

```
┌─────────────────────────────────────────────────────────────────┐
│                    QUY TRÌNH PHÁT TRIỂN VỚI AI AGENT           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ 1. Khởi  │───►│ 2. Phân  │───►│ 3. Thiết │───►│ 4. Triển │  │
│  │   tạo    │    │   tích   │    │   kế     │    │   khai   │  │
│  │  dự án   │    │  yêu cầu │    │ kiến trúc│    │ mã nguồn │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│                                                       │         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐         │         │
│  │ 8. Triển │◄───│ 7. Review│◄───│ 6. CI/CD │◄───────┘         │
│  │   khai & │    │ & Cải    │    │ Tự động  │                   │
│  │ vận hành │    │   tiến   │    │   hóa    │                   │
│  └──────────┘    └──────────┘    └──────────┘                   │
│                       │                                         │
│                       ▼                                         │
│               ┌──────────────┐                                  │
│               │  5. Kiểm thử │                                  │
│               │  (Testing)   │                                  │
│               └──────────────┘                                  │
│                                                                 │
│  ◄──────── Vòng lặp cải tiến liên tục (Iterative) ──────────►  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Best Practices khi làm việc với AI Agent

### Nên làm (DO)

| # | Thực hành | Lý do |
|---|---|---|
| 1 | **Chia nhỏ task** | AI Agent hoạt động tốt nhất với task cụ thể, rõ ràng |
| 2 | **Cung cấp context đầy đủ** | Đính kèm file liên quan, interface, requirement khi prompt |
| 3 | **Review kết quả từng bước** | Không tin tưởng mù quáng, luôn kiểm tra output |
| 4 | **Commit thường xuyên** | Commit sau mỗi thay đổi có ý nghĩa để dễ rollback |
| 5 | **Dùng iterative approach** | Nhiều vòng review ngắn tốt hơn 1 vòng dài |
| 6 | **Viết prompt rõ ràng** | Mô tả chính xác input, output, constraints |
| 7 | **Lưu prompt hiệu quả** | Ghi lại prompt đã dùng để tái sử dụng (`prompt.md`) |
| 8 | **Tạo agent log** | Ghi lại lịch sử thao tác của AI Agent (`agent.log`) |

### Không nên làm (DON'T)

| # | Tránh | Lý do |
|---|---|---|
| 1 | **Prompt quá chung chung** | AI sẽ trả output thiếu chi tiết, không đúng ý |
| 2 | **Bỏ qua review** | AI có thể tạo code/diagram có lỗi logic |
| 3 | **Merge không test** | Code AI tạo có thể chưa pass tất cả edge cases |
| 4 | **Quá phụ thuộc AI** | AI là công cụ hỗ trợ, không thay thế tư duy kiến trúc |
| 5 | **Commit code chứa secret** | Luôn check trước khi commit: API key, password |
| 6 | **Prompt chứa dữ liệu nhạy cảm** | Không đưa credentials, PII vào prompt |

---

## Áp dụng thực tế — Dự án CAN-HMI

Dự án CAN-HMI đã áp dụng quy trình trên với kết quả cụ thể:

| Giai đoạn | Kết quả | Số vòng lặp |
|---|---|---|
| 1. Khởi tạo | Repo + cấu trúc thư mục + config | 1 |
| 2. Phân tích yêu cầu | `requirement.md` v0.6.0 (11 sections, 16 AC, NFR) | 5 vòng review |
| 3. Thiết kế kiến trúc | 15 PlantUML diagrams (C4, class, sequence, ER, state, ...) | 20 + 5 vòng review |
| 4–8 | Đang triển khai... | — |

### Timeline thực tế

```
Ngày 1:  Giai đoạn 1 + 2 (khởi tạo + requirement draft)
Ngày 2:  Giai đoạn 2 tiếp (5 vòng review requirement)
Ngày 3:  Giai đoạn 3 (tạo 14 diagrams + 20 vòng review)
Ngày 4:  Giai đoạn 3 tiếp (5 vòng review mới sau cập nhật requirement)
Ngày 5+: Giai đoạn 4 (implementation bắt đầu)
```

---

## Tham khảo

- [GitHub Copilot Docs](https://docs.github.com/en/copilot)
- [C4 Model](https://c4model.com/)
- [PlantUML](https://plantuml.com/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [python-can](https://python-can.readthedocs.io/)
