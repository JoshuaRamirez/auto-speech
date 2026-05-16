"""CLI: read JSONL events from stdin, print phase transitions to stdout.

Used for inspecting the classifier against a captured events log:

    cat /tmp/auto-speech-narrator-events.jsonl | python narrator_classify_cli.py
"""
from __future__ import annotations

import json
import sys

from narrator_phase_classifier import PhaseClassifier


def main() -> int:
    clf = PhaseClassifier()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        closed = clf.feed(ev)
        if closed is not None:
            _print_phase(closed)
    final = clf.flush()
    if final is not None:
        _print_phase(final, flush=True)
    return 0


def _print_phase(p, flush: bool = False) -> None:
    tag = " [flush]" if flush else ""
    print(
        f"PHASE {p.category.value}{tag}  events={len(p.events)}  "
        f"duration={p.duration_s:.1f}s"
    )
    for e in p.events:
        print(f"  - {e.summary}")


if __name__ == "__main__":
    sys.exit(main())
