"""HealthReport — the value object the doctor produces.

A flat list of named Checks, each carrying a Status and a one-line detail.
Aggregation rule: the system is UNHEALTHY iff any check FAILed (warnings are
degradations, not failures), which drives the process exit code so the
command is usable in scripts and monitors.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class HealthReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: Status, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def healthy(self) -> bool:
        """Warnings are tolerated; only a FAIL makes the system unhealthy."""
        return all(c.status is not Status.FAIL for c in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.healthy else 1

    def to_text(self) -> str:
        tag = {Status.OK: "OK  ", Status.WARN: "WARN", Status.FAIL: "FAIL"}
        header = "auto-speech doctor — " + ("HEALTHY" if self.healthy else "UNHEALTHY")
        lines = [f"  [{tag[c.status]}] {c.name}: {c.detail}" for c in self.checks]
        return "\n".join([header, *lines])

    def to_json(self) -> str:
        return json.dumps(
            {
                "healthy": self.healthy,
                "checks": [
                    {"name": c.name, "status": c.status.value, "detail": c.detail}
                    for c in self.checks
                ],
            },
            indent=2,
        )
