from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import signal
import threading
import time
from typing import Generator

import pytest


REPORT_DEFAULT = "./reports/test_case_realtime_vi.log"
CASE_TIMEOUT_SECONDS = 300


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _phase_vi(phase: str) -> str:
    return {
        "setup": "chuẩn bị",
        "call": "thực thi",
        "teardown": "dọn dẹp",
    }.get(phase, phase)


def _normalize_spaces(text: str) -> str:
    return " ".join(text.strip().split())


def _describe_test_case_vi(item: pytest.Item) -> str:
    token_map = {
        "load": "nạp",
        "loads": "nạp",
        "decode": "giải mã",
        "encode": "mã hóa",
        "verify": "xác minh",
        "check": "kiểm tra",
        "validate": "xác thực dữ liệu",
        "should": "phải",
        "returns": "trả về",
        "return": "trả về",
        "missing": "thiếu",
        "unknown": "không xác định",
        "raises": "phát sinh lỗi",
        "roundtrip": "vòng lặp mã hóa-giải mã",
        "little": "little",
        "endian": "endian",
        "signed": "có dấu",
        "unsigned": "không dấu",
        "insert": "chèn",
        "extract": "trích xuất",
        "custom": "tùy chỉnh",
        "auto": "tự động",
        "allocate": "cấp phát",
        "start": "bắt đầu",
        "bit": "bit",
        "bits": "bit",
        "message": "thông điệp",
        "msg": "thông điệp",
        "frame": "khung",
        "json": "JSON",
        "none": "None",
        "empty": "rỗng",
        "auth": "xác thực",
        "api": "API",
        "ws": "WebSocket",
        "websocket": "WebSocket",
        "signal": "tín hiệu",
        "signals": "tín hiệu",
        "profile": "hồ sơ",
        "profiles": "hồ sơ",
        "alarm": "cảnh báo",
        "alarms": "cảnh báo",
        "config": "cấu hình",
        "health": "sức khỏe hệ thống",
        "ready": "trạng thái sẵn sàng",
        "camera": "camera",
        "stream": "luồng",
        "metric": "chỉ số",
        "metrics": "chỉ số",
        "devmode": "chế độ bảo trì",
        "runtime": "chạy thực tế",
        "integration": "tích hợp",
        "security": "bảo mật",
        "injection": "tiêm đầu vào",
        "bypass": "vượt qua kiểm soát",
        "bus": "CAN bus",
        "parser": "bộ phân tích",
        "processor": "bộ xử lý",
        "storage": "lưu trữ",
        "core": "lõi hệ thống",
    }

    raw_name = item.name
    if raw_name.startswith("test_"):
        raw_name = raw_name[len("test_") :]

    words = [w for w in raw_name.replace("-", "_").split("_") if w]
    converted = [token_map.get(word.lower(), word) for word in words]
    readable = " ".join(converted)
    if not readable:
        readable = item.name

    return f"Mô tả tự động: Kiểm tra {readable}."


def _ensure_report_file(config: pytest.Config) -> Path:
    report_file = Path(config.getoption("--case-report-file")).resolve()
    report_file.parent.mkdir(parents=True, exist_ok=True)
    return report_file


def _append_report_line(config: pytest.Config, line: str) -> None:
    report_file = _ensure_report_file(config)
    with report_file.open("a", encoding="utf-8") as fh:
        fh.write(f"{line}\n")
    print(line, flush=True)


class _CaseTimeoutError(TimeoutError):
    pass


def _get_rt_state(item: pytest.Item) -> dict[str, str | float]:
    state = getattr(item, "_rt_state", None)
    if state is None:
        state = {}
        setattr(item, "_rt_state", state)
    return state


@contextmanager
def _timeout_guard(seconds: int, nodeid: str, phase: str) -> Generator[None, None, None]:
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _raise_timeout(signum: int, frame: object) -> None:  # noqa: ARG001
        raise _CaseTimeoutError(
            f"Timeout {seconds}s khi test đang ở pha '{_phase_vi(phase)}' ({nodeid})."
        )

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
        signal.signal(signal.SIGALRM, previous_handler)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--case-report-file",
        action="store",
        default=REPORT_DEFAULT,
        help="File report realtime theo tung test case (tieng Viet).",
    )
    parser.addoption(
        "--case-timeout-seconds",
        action="store",
        default=str(CASE_TIMEOUT_SECONDS),
        help="Timeout cho moi test case (giay). Mac dinh: 300.",
    )
    parser.addoption(
        "--case-report-append",
        action="store_true",
        default=False,
        help="Neu bat, se append vao file report thay vi ghi de moi lan chay.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "vi_desc(text): Mo ta tieng Viet cho test case trong report realtime.",
    )
    report_file = _ensure_report_file(config)
    header = (
        f"\n=== REALTIME TEST REPORT (VI) | {_now_text()} | "
        f"timeout={config.getoption('--case-timeout-seconds')}s ==="
    )
    mode = "a" if config.getoption("--case-report-append") else "w"
    with report_file.open(mode, encoding="utf-8") as fh:
        fh.write(f"{header}\n")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, object, None]:  # noqa: ARG001
    del nextitem
    start = time.perf_counter()
    start_text = _now_text()

    state = _get_rt_state(item)
    state["rt_start"] = start
    state["rt_start_text"] = start_text
    state["rt_outcome"] = "passed"
    state["rt_reason"] = ""

    vi_desc_marker = item.get_closest_marker("vi_desc")
    if vi_desc_marker and vi_desc_marker.args:
        vi_desc = f"Mô tả: {_normalize_spaces(str(vi_desc_marker.args[0]))}"
    else:
        vi_desc = _describe_test_case_vi(item)

    _append_report_line(item.config, f"[BAT DAU] {item.nodeid}")
    _append_report_line(item.config, f"  - Start: {start_text}")
    _append_report_line(item.config, f"  - {vi_desc}")

    outcome = yield
    _ = outcome

    end = time.perf_counter()
    end_text = _now_text()
    duration = end - start
    state = _get_rt_state(item)
    status = str(state.get("rt_outcome", "passed")).upper()
    reason = str(state.get("rt_reason", ""))

    _append_report_line(item.config, f"[KET THUC] {item.nodeid}")
    _append_report_line(item.config, f"  - End: {end_text}")
    _append_report_line(item.config, f"  - Duration: {duration:.3f}s")
    _append_report_line(item.config, f"  - Trang thai: {status}")
    if reason:
        _append_report_line(item.config, f"  - Chi tiet: {reason}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, None]:
    timeout_seconds = int(item.config.getoption("--case-timeout-seconds"))
    with _timeout_guard(timeout_seconds, item.nodeid, "setup"):
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, None]:
    timeout_seconds = int(item.config.getoption("--case-timeout-seconds"))
    with _timeout_guard(timeout_seconds, item.nodeid, "call"):
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None, object, None]:  # noqa: ARG001
    del nextitem
    timeout_seconds = int(item.config.getoption("--case-timeout-seconds"))
    with _timeout_guard(timeout_seconds, item.nodeid, "teardown"):
        yield


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> None:
    state = _get_rt_state(item)
    if call.excinfo and call.excinfo.errisinstance(_CaseTimeoutError):
        state["rt_outcome"] = "failed"
        state["rt_reason"] = str(call.excinfo.value)
        return

    if call.when == "setup" and call.excinfo:
        if call.excinfo.errisinstance(pytest.skip.Exception):
            state["rt_outcome"] = "skipped"
            state["rt_reason"] = call.excinfo.exconly(tryshort=True)
        else:
            state["rt_outcome"] = "failed"
            state["rt_reason"] = f"Loi setup: {call.excinfo.exconly(tryshort=True)}"
    elif call.when == "call":
        if call.excinfo:
            if call.excinfo.errisinstance(pytest.skip.Exception):
                state["rt_outcome"] = "skipped"
                state["rt_reason"] = call.excinfo.exconly(tryshort=True)
            else:
                state["rt_outcome"] = "failed"
                state["rt_reason"] = call.excinfo.exconly(tryshort=True)
        elif state.get("rt_outcome") == "passed":
            state["rt_outcome"] = "passed"
    elif call.when == "teardown" and call.excinfo:
        if call.excinfo.errisinstance(pytest.skip.Exception):
            state["rt_outcome"] = "skipped"
            state["rt_reason"] = call.excinfo.exconly(tryshort=True)
        else:
            state["rt_outcome"] = "failed"
            state["rt_reason"] = f"Loi teardown: {call.excinfo.exconly(tryshort=True)}"