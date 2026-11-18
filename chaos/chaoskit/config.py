"""Load and validate the scenario catalogue."""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


@dataclass(frozen=True)
class Check:
    name: str
    query: str
    op: str
    value: float

    def holds(self, observed: float | None) -> bool:
        # A missing series means the metric could not be scraped, which is
        # itself a symptom of failure - never treat it as healthy.
        if observed is None:
            return False
        return OPS[self.op](observed, self.value)


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    category: str
    description: str
    action: str
    duration_s: int
    expected_alerts: list[str]
    runbook: str
    params: dict[str, Any] = field(default_factory=dict)
    target_node: str = ""


@dataclass(frozen=True)
class Settings:
    poll_interval_s: int
    detect_timeout_s: int
    recover_timeout_s: int
    stable_polls: int


@dataclass(frozen=True)
class Catalogue:
    settings: Settings
    steady_state: list[Check]
    scenarios: list[Scenario]

    def get(self, scenario_id: str) -> Scenario:
        for s in self.scenarios:
            if s.id == scenario_id or s.name == scenario_id:
                return s
        raise KeyError(f"unknown scenario: {scenario_id}")


class ConfigError(ValueError):
    pass


def load(path: str | Path) -> Catalogue:
    raw = yaml.safe_load(Path(path).read_text())
    defaults = raw.get("defaults", {})

    settings = Settings(
        poll_interval_s=int(defaults.get("poll_interval_s", 5)),
        detect_timeout_s=int(defaults.get("detect_timeout_s", 300)),
        recover_timeout_s=int(defaults.get("recover_timeout_s", 600)),
        stable_polls=int(defaults.get("stable_polls", 3)),
    )

    checks = []
    for c in raw.get("steady_state", []):
        if c["op"] not in OPS:
            raise ConfigError(f"check {c['name']}: unsupported op {c['op']!r}")
        checks.append(Check(c["name"], c["query"].strip(), c["op"], float(c["value"])))

    scenarios = []
    for s in raw.get("scenarios", []):
        scenarios.append(Scenario(
            id=s["id"],
            name=s["name"],
            category=s["category"],
            description=s["description"],
            action=s["action"],
            duration_s=int(s.get("duration_s", 0)),
            expected_alerts=list(s.get("expected_alerts", [])),
            runbook=s["runbook"],
            params=dict(s.get("params", {})),
            target_node=s.get("target_node", defaults.get("target_node", "")),
        ))

    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ConfigError("scenario ids must be unique")
    if not checks:
        raise ConfigError("at least one steady_state check is required")

    return Catalogue(settings, checks, scenarios)
