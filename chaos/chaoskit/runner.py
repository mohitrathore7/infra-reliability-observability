"""Run one experiment: verify steady state -> inject -> detect -> revert -> recover.

Timeline recorded for every run (seconds, relative to injection):

    t_detect   first poll where steady state broke OR an expected alert fired
    t_revert   when the fault was removed (injection + duration_s)
    t_recover  first poll of `stable_polls` consecutive healthy polls

    time_to_detect   = t_detect
    time_to_recover  = t_recover                   (total impact window)
    mttr             = t_recover - t_revert        (self-healing after fault removal)
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .actions import Executor, Target, build_plan, execute
from .config import Catalogue, Check, Scenario


class Probe:
    """What the runner needs from Prometheus (lets tests plug in a fake)."""

    def scalar(self, query: str) -> float | None: ...
    def firing_alerts(self) -> set[str]: ...


@dataclass
class Observation:
    t: float
    failing_checks: list[str]
    firing_alerts: list[str]


@dataclass
class Result:
    scenario_id: str
    scenario_name: str
    target_node: str
    started_at: str
    status: str = "pending"            # passed | not_detected | not_recovered | aborted
    time_to_detect_s: float | None = None
    time_to_recover_s: float | None = None
    mttr_s: float | None = None
    detected_by: list[str] = field(default_factory=list)
    alerts_seen: list[str] = field(default_factory=list)
    expected_alerts_missing: list[str] = field(default_factory=list)
    timeline: list[Observation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(checks: list[Check], probe: Probe) -> list[str]:
    return [c.name for c in checks if not c.holds(probe.scalar(c.query))]


class Runner:
    def __init__(self, catalogue: Catalogue, probe: Probe, executor: Executor,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.cat = catalogue
        self.probe = probe
        self.ex = executor
        self.clock = clock
        self.sleep = sleep

    def _observe(self, t0: float) -> Observation:
        return Observation(
            t=round(self.clock() - t0, 1),
            failing_checks=evaluate(self.cat.steady_state, self.probe),
            firing_alerts=sorted(self.probe.firing_alerts()),
        )

    def run(self, s: Scenario, target: Target) -> Result:
        cfg = self.cat.settings
        res = Result(s.id, s.name, target.node,
                     datetime.now(timezone.utc).isoformat(timespec="seconds"))

        # 1. Steady-state hypothesis must hold before we break anything.
        failing = evaluate(self.cat.steady_state, self.probe)
        if failing:
            res.status = "aborted"
            res.notes.append(f"steady state not met before injection: {', '.join(failing)}")
            return res

        plan = build_plan(s, target)
        ctx: dict[str, str] = {}

        # 2. Inject.
        t0 = self.clock()
        execute(plan.inject, self.ex, ctx)
        alerts_seen: set[str] = set()
        reverted = not plan.revert   # nothing to revert -> fault is instantaneous
        t_revert = 0.0 if reverted else None

        try:
            # 3. Detect + hold the fault for duration_s.
            hold_until = s.duration_s
            detect_deadline = max(cfg.detect_timeout_s, hold_until)
            while True:
                obs = self._observe(t0)
                res.timeline.append(obs)
                alerts_seen.update(obs.firing_alerts)
                expected_firing = [a for a in obs.firing_alerts if a in s.expected_alerts]
                if res.time_to_detect_s is None and (obs.failing_checks or expected_firing):
                    res.time_to_detect_s = obs.t
                    res.detected_by = obs.failing_checks + expected_firing
                if obs.t >= hold_until and (res.time_to_detect_s is not None
                                            or obs.t >= detect_deadline):
                    break
                self.sleep(cfg.poll_interval_s)

            # 4. Revert.
            if not reverted:
                execute(plan.revert, self.ex, ctx)
                t_revert = round(self.clock() - t0, 1)
                reverted = True

            # 5. Wait for steady state to hold `stable_polls` times in a row.
            healthy_streak, first_healthy = 0, None
            while self.clock() - t0 < t_revert + cfg.recover_timeout_s:
                obs = self._observe(t0)
                res.timeline.append(obs)
                alerts_seen.update(obs.firing_alerts)
                if not obs.failing_checks:
                    healthy_streak += 1
                    first_healthy = first_healthy if first_healthy is not None else obs.t
                    if healthy_streak >= cfg.stable_polls:
                        res.time_to_recover_s = first_healthy
                        res.mttr_s = round(max(0.0, first_healthy - t_revert), 1)
                        break
                else:
                    healthy_streak, first_healthy = 0, None
                self.sleep(cfg.poll_interval_s)
        finally:
            # Never leave a fault behind, even on Ctrl-C or an exception.
            if not reverted:
                try:
                    execute(plan.revert, self.ex, ctx)
                    res.notes.append("fault reverted in cleanup handler")
                except Exception as exc:  # noqa: BLE001
                    res.notes.append(f"CLEANUP FAILED - revert manually: {exc}")

        res.alerts_seen = sorted(alerts_seen)
        res.expected_alerts_missing = sorted(set(s.expected_alerts) - alerts_seen)

        if res.time_to_recover_s is None:
            res.status = "not_recovered"
        elif res.time_to_detect_s is None:
            res.status = "not_detected"
        else:
            res.status = "passed"
        return res
