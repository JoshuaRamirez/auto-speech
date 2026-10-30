"""tests/e2e scripts run with tests/e2e first on sys.path: this loads the
suite's own bootstrap, tests/isolation.py, under the same name, so
`import isolation` isolates them as it does every other test (#m1030-1241-6e0a)."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, _TESTS)
try:
    del sys.modules[__name__]  # the import below finds tests/isolation.py, not this file
    import isolation as _isolation  # noqa: E402
finally:
    if sys.path and sys.path[0] == _TESTS:
        sys.path.pop(0)
sys.modules[__name__] = _isolation
