# Trình tạo cấu hình từ DBC

Thư mục này chứa các tiện ích dùng để tạo/gộp các file cấu hình tĩnh từ file DBC.

Yêu cầu
 - Python 3.8+
 - Cài phụ thuộc runtime:

```bash
pip install cantools pyyaml
```

Các file chính
 - `dbc_utils.py` — hàm tiện ích chung: phân tích DBC và đọc/ghi JSON.
 - `gen_signals_from_dbc.py` — tạo/gộp `config/signals.json` từ DBC.
 - `gen_alarms_from_dbc.py` — tạo/gộp `config/alarms.json` từ DBC.
 - `gen_configs_from_dbc.py` — script kết hợp (giữ lại để tiện lợi).

Các ví dụ sử dụng cơ bản

- Chạy chế độ dry-run (hiển thị những gì sẽ thêm, không ghi file):

```bash
python scripts/gen_signals_from_dbc.py -d path/to/dbc_or_dir --dry-run
python scripts/gen_alarms_from_dbc.py -d path/to/dbc_or_dir --dry-run
```

- Tạo và ghi ra các đường dẫn cấu hình mặc định:

```bash
python scripts/gen_signals_from_dbc.py -d path/to/dbc_dir
python scripts/gen_alarms_from_dbc.py -d path/to/dbc_dir
```

- Chỉ định đường dẫn đầu ra và cho phép ghi đè:

```bash
python scripts/gen_signals_from_dbc.py -d path/to/file.dbc --out config/signals.json --overwrite
python scripts/gen_alarms_from_dbc.py -d path/to/file.dbc --out config/alarms.json --overwrite
```

Ghi chú
 - Các script sử dụng `cantools` để phân tích DBC; chúng trích các thuộc tính `name`, `minimum`, `maximum`, và `unit` (nếu có).
 - Các trình tạo sẽ không xóa hoặc thay đổi mục hiện có trừ khi bạn truyền `--overwrite`.
 - YAML sinh ra sử dụng các giá trị mặc định đơn giản:
   - Tín hiệu (`signals`): `display_name` = tên tín hiệu, `group` = `unknown`, `widget` = `gauge`, `writable` = `false`.
   - Cảnh báo (`alarms`): `warning_high`/`warning_low` được lấy từ DBC `maximum`/`minimum` nếu có; ngưỡng `critical` để `null` mặc định.
 - Vui lòng kiểm tra các file đã sinh và điều chỉnh ngưỡng/nhóm/widget phù hợp với dự án.

Ví dụ (Windows PowerShell)

```powershell
python .\scripts\gen_signals_from_dbc.py -d .\db\ -v --dry-run
python .\scripts\gen_alarms_from_dbc.py -d .\db\ --out config\alarms.json
```

Tùy chọn nâng cao tôi có thể hỗ trợ:
 - Thêm một script `--apply-both` chạy cả hai trình tạo trong một lệnh.
 - Cài heuristic ngưỡng thông minh hơn (ví dụ: warning = 75% của max, critical = 95%).
 - Thêm unit test cho các script tạo.
