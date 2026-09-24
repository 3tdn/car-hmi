# Test Layout

This project organizes tests by objective to make scope and ownership clear.

## Structure

- `tests/1_unit_functions/`: fast unit tests around isolated modules/functions.
- `tests/2_functional_tests/api/`: API behavior and route-level integration tests.
- `tests/2_functional_tests/websockets/`: WebSocket protocol and realtime delivery tests.
- `tests/2_functional_tests/integration/`: multi-component end-to-end flows.
- `tests/2_functional_tests/runtime/`: full app runtime smoke (start app process then call API/WS).
- `tests/2_functional_tests/tools/`: helper scripts for manual protocol checks.
- `tests/3_performance/scripts/`: load/performance scripts (k6/Locust/JMeter).
- `tests/3_performance/reports/`: generated performance reports (git-ignored).
- `tests/4_security/injection/`: input-injection hardening tests.
- `tests/4_security/auth_bypass/`: auth/authz bypass regression tests.
- `tests/5_scenario/`: multi-step business scenario tests (signal read/write/subscribe,
  profile permission switching, Dev Mode seat restraint control). See
  `tests/5_scenario/conftest.py` for shared `app_builder`/`app_builder_sync` fixtures.

## Quick Commands

Run all tests:

```bash
pytest tests
```

Run only unit tests:

```bash
pytest tests/1_unit_functions
```

Run only functional tests:

```bash
pytest tests/2_functional_tests
```

Run only security tests:

```bash
pytest tests/4_security
```

Run only scenario tests:

```bash
pytest tests/5_scenario
```

Run runtime smoke tests (opt-in):

```bash
RUN_RUNTIME_SMOKE=1 pytest tests/2_functional_tests/runtime/test_runtime_smoke.py
```

Run k6 performance script:

```bash
BASE_URL=http://localhost:8000 k6 run tests/3_performance/scripts/load_test_homepage.js
```

## Realtime Per-Test Report (Vietnamese)

Each test case now logs before/after execution with:

- start time
- end time
- duration
- status (`PASSED`/`FAILED`/`SKIPPED`)
- Vietnamese explanation of test intent

Default report file:

```bash
reports/test_case_realtime_vi.log
```

Run with 5-minute timeout per test case:

```bash
pytest tests --case-timeout-seconds=300 --case-report-file=reports/test_case_realtime_vi.log
```

Runtime smoke with the same realtime reporting:

```bash
RUN_RUNTIME_SMOKE=1 pytest tests/2_functional_tests/runtime --case-timeout-seconds=300 --case-report-file=reports/test_case_realtime_vi.log
```
