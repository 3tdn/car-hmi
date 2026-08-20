# Test Layout

This project organizes tests by objective to make scope and ownership clear.

## Structure

- `tests/1_unit_functions/`: fast unit tests around isolated modules/functions.
- `tests/2_functional_tests/api/`: API behavior and route-level integration tests.
- `tests/2_functional_tests/websockets/`: WebSocket protocol and realtime delivery tests.
- `tests/2_functional_tests/integration/`: multi-component end-to-end flows.
- `tests/2_functional_tests/tools/`: helper scripts for manual protocol checks.
- `tests/3_performance/scripts/`: load/performance scripts (k6/Locust/JMeter).
- `tests/3_performance/reports/`: generated performance reports (git-ignored).
- `tests/4_security/injection/`: input-injection hardening tests.
- `tests/4_security/auth_bypass/`: auth/authz bypass regression tests.

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

Run k6 performance script:

```bash
BASE_URL=http://localhost:8000 k6 run tests/3_performance/scripts/load_test_homepage.js
```
