# Test Suite

Run the two test layers in separate processes from the repository root:

```bash
pytest backend/tests --ignore=backend/tests/invariants
pytest backend/tests/invariants
```

Several legacy integration modules configure a temporary database through process-wide environment variables at import time. Keeping the layers in separate pytest processes prevents their database engines and application imports from contaminating each other.

Test ownership is split by purpose:

- `tests/test_*.py`: behavior, API, integration, and regression tests.
- `tests/invariants/test_*.py`: structural and cross-storage contracts that must remain true across refactors.
- `engine/tools/system_test.py`: explicit mock-engine integration check; it is not collected by pytest.

Useful focused commands:

```bash
pytest backend/tests/test_outline_api.py
pytest backend/tests/invariants
cd backend && python -m engine.tools.system_test
```

Maintenance rules:

- Add a regression to the closest existing domain file before creating a new file.
- Name tests after behavior, not implementation phases or iteration numbers.
- Do not add compatibility re-export test modules; pytest will collect imported tests twice.
- Keep environment and database configuration inside fixtures. New tests must not add more import-time global state.
- Tests must create their own fixtures and must not read local runtime data during collection.
- Large real-model benchmarks belong outside the default pytest suite and should not commit generated output.
