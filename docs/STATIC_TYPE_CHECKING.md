# Static type-checking baseline

## Decision

HIVE retains Python 3.11 as its declared compatibility floor and runs mypy in
strict mode over every module under `src/hive`. Development environments may
use a newer interpreter, so the development dependency set constrains NumPy to
`<2.5`: NumPy 2.5's inline stubs use Python 3.12 `type` statement syntax and
cannot be parsed when mypy intentionally checks the Python 3.11 target.

This is a development-tool compatibility constraint, not an exclusion of HIVE
modules. The complete command is:

```powershell
uv run --frozen mypy src/hive
```

## Verified result (24 August 2026)

- Strict mypy: success across all 79 HIVE source files.
- Ruff: success across source and tests.
- Pytest: 352 passed, 1 skipped, with the existing Starlette deprecation warning.
- JavaScript syntax and Docker Compose configuration: success.
- Lockfile validation: success with NumPy 2.4.6 selected for the development environment.
- Rebuilt Python 3.11 deployment: NumPy 2.4.6 and Torch 2.12.1+cpu; real GLiNER,
  case intelligence, Telethon, and control bot all ready in 42.37 seconds.
- Live PostgreSQL and Qdrant write/read/search/cleanup round trips: success.

The work corrected real application contracts rather than adding blanket
module exclusions. Notable examples include:

- the hybrid PostgreSQL/Qdrant store now fully implements
  `CaseIntelligenceStore.list_profiles()`;
- RSA evidence operations reject non-RSA keys before signing or verification;
- Telegram callbacks, asynchronous tasks, session state, web API payloads, and
  runtime responses have explicit types;
- database inventory reads handle a missing cursor row safely;
- message roles are narrowed to the literals accepted by the session model;
- hash-chain payloads, evaluation records, and case-vector interfaces carry
  concrete generic arguments.

One targeted `untyped-decorator` ignore remains on Telethon's dynamically
generated `client.on(...)` decorator. The decorated handler itself and its
inputs are annotated; the ignore is limited to the external SDK boundary.

## Maintenance rule

1. Run mypy through the locked development environment, not a globally
   installed interpreter.
2. Keep `python_version = "3.11"` aligned with `requires-python` while Python
   3.11 support is claimed.
3. Do not remove the NumPy compatibility constraint until its stubs parse for
   the configured mypy target or HIVE deliberately raises its Python floor.
4. Prefer typed protocols, narrowing, and concrete collection types over
   `Any`; use a local ignore only for a verified dynamic third-party boundary.
5. Require mypy, Ruff, pytest, JavaScript syntax, lock validation, and Compose
   validation before accepting dependency or typing changes.
