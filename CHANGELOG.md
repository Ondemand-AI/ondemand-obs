# Changelog

Versions up to 0.1.14 are described in their commit messages (`git log`).

## [0.1.15] - 2026-11-07

### Fixed

- `import ondemand_obs` failed on Python 3.9 (`TypeError: unsupported operand
  type(s) for |`), although the package declares `requires-python >=3.9`.
  `__init__.py` annotated `_dist`, `configure_observability` and
  `get_otlp_log_handler` with `X | None`, which 3.9 evaluates at import time.
  They now use `typing.Optional`. Since ondemand-ai's worker imports
  `ondemand_obs` inside `try/except ImportError`, the TypeError escaped and
  `OndemandWorker()` crashed on 3.9.
- `tests/test_py39_compat.py` reads the package source and fails on any PEP 604
  union or PEP 585 builtin generic in an annotation, a `match` statement, or
  `isinstance(x, A | B)`, so the next one is caught on any interpreter.
- The OpenAI instrumentation test now skips when the `[llm]` extra is absent
  (always the case on 3.9) instead of failing on the import, so the suite runs
  green on 3.9.

### Docs

- CLAUDE.md: robots get `SENTRY_DSN` from their own key (`<process_id>`) in the
  `ondemand-sentry` secret; the `ondemand-shared` key arrives as
  `SENTRY_DSN_FALLBACK`. The hub exports to our self-hosted HyperDX v1 and
  Better Stack (hyperdx.io Cloud and the HyperDX v2 stack are gone), also in
  README.md and the `config.py` comment.
