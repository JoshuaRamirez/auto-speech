"""End-to-End (E2E) Test Suite for auto-speech Unified Daemon Server.

Imported as a package (`python -m unittest tests.e2e...` from the project
root), each test module's `import isolation` finds the suite's bootstrap here
before any auto-speech module loads (#m1030-1241-6e0a)."""

from . import isolation as _isolation  # noqa: F401  -- registers itself as `isolation`
