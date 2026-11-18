from pathlib import Path

import pytest

from chaoskit import report
from chaoskit.actions import DryRunExecutor, Target, build_plan, execute
from chaoskit.cli import main, resolve_target
from chaoskit.config import Check, load
from chaoskit.runner import Runner

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = load(ROOT / "chaos" / "scenarios.yaml")
TARGET = Target(node="infra-reliability-worker-2", ssh_host="203.0.113.7")


# ------------------------------------------------------------------ config

def test_catalogue_has_12_unique_scenarios():
    assert len(CATALOGUE.scenarios) == 12
    assert len({s.id for s in CATALOGUE.scenarios}) == 12


def test_every_scenario_links_an_existing_runbook_section():
    for s in CATALOGUE.scenarios:
        path = s.runbook.partition("#")[0]
        text = (ROOT / path).read_text().lower()
        assert f"## {s.id.lower()}" in text, f"{s.id} missing from {path}"


def test_expected_alerts_exist_in_rules():
    rules = (ROOT / "monitoring" / "rules" / "reliability-alerts.yaml").read_text()
    for s in CATALOGUE.scenarios:
        for alert in s.expected_alerts:
            assert f"alert: {alert}" in rules, f"{s.id} expects unknown alert {alert}"


@pytest.mark.parametrize("observed,expected", [(None, False), (0, True), (1, False)])
def test_missing_metric_is_never_healthy(observed, expected):
    assert Check("x", "q", "==", 0).holds(observed) is expected


# ----------------------------------------------------------------- actions

@pytest.mark.parametrize("scenario", CATALOGUE.scenarios, ids=lambda s: s.id)
def test_every_scenario_builds_a_plan(scenario):
    plan = build_plan(scenario, TARGET)
    assert plan.inject


def test_network_faults_install_deadman_switch():
    for sid in ("S05", "S06", "S07", "S10"):
        plan = build_plan(CATALOGUE.get(sid), TARGET)
        assert "sleep" in plan.inject[0][-1], f"{sid} has no dead-man switch"


def test_pod_kill_substitutes_pod_name():
    class Fake(DryRunExecutor):
        def run(self, argv):
            super().run(argv)
            return "events-dual-role-1\n" if "get" in argv else ""

    ex = Fake()
    execute(build_plan(CATALOGUE.get("S11"), TARGET).inject, ex, {})
    assert ex.calls[1][-3] == "events-dual-role-1"


def test_disk_fill_awk_braces_survive_substitution():
    ex = DryRunExecutor()
    execute(build_plan(CATALOGUE.get("S10"), TARGET).inject, ex, {})
    assert "{printf" in ex.calls[1][-1]


def test_ec2_instance_id_is_captured_and_reused():
    class Fake(DryRunExecutor):
        def run(self, argv):
            super().run(argv)
            return "i-0abc123" if "describe-instances" in argv else ""

    ex, ctx = Fake(), {}
    plan = build_plan(CATALOGUE.get("S01"), TARGET)
    execute(plan.inject, ex, ctx)
    execute(plan.revert, ex, ctx)
    assert ex.calls[1][-1] == "i-0abc123"
    assert ex.calls[2][-1] == "i-0abc123"


# ------------------------------------------------------------------ runner

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


class ScriptedProbe:
    """Steady state breaks in [break_at, heal_at) seconds after injection."""

    def __init__(self, clock, break_at, heal_at, alert=None):
        self.clock, self.break_at, self.heal_at, self.alert = clock, break_at, heal_at, alert
        self.t0 = None

    def _broken(self):
        if self.t0 is None:
            return False
        t = self.clock() - self.t0
        return self.break_at <= t < self.heal_at

    def scalar(self, query):
        if "aliveWorkers" in query:
            return 2.0 if self._broken() else 3.0
        if "Ready" in query and "kube_node" in query:
            return 4.0
        if "ActiveNodeCount" in query:
            return 4.0
        return 0.0

    def firing_alerts(self):
        return {self.alert} if self.alert and self._broken() else set()


class ArmingExecutor(DryRunExecutor):
    """Marks injection time on the probe when the first command runs."""

    def __init__(self, probe, clock):
        super().__init__()
        self.probe, self.clock = probe, clock

    def run(self, argv):
        if self.probe.t0 is None:
            self.probe.t0 = self.clock()
        return super().run(argv)


def make_runner(break_at, heal_at, alert=None):
    clock = FakeClock()
    probe = ScriptedProbe(clock, break_at, heal_at, alert)
    ex = ArmingExecutor(probe, clock)
    return Runner(CATALOGUE, probe, ex, clock=clock, sleep=clock.sleep), ex


def test_run_measures_detect_and_recover():
    runner, ex = make_runner(break_at=10, heal_at=200, alert="NodeNotReady")
    r = runner.run(CATALOGUE.get("S02"), TARGET)  # duration 180s, revertible
    assert r.status == "passed"
    assert r.time_to_detect_s == 10
    assert r.time_to_recover_s == 200
    assert r.mttr_s == pytest.approx(20)
    assert "spark_all_workers_alive" in r.detected_by
    assert any("systemctl start" in c[-1] for c in ex.calls)  # reverted


def test_undetected_fault_is_reported_as_gap():
    runner, _ = make_runner(break_at=10**9, heal_at=10**9 + 1)
    r = runner.run(CATALOGUE.get("S06"), TARGET)
    assert r.status == "not_detected"
    assert r.expected_alerts_missing == ["KafkaConsumerLagHigh"]


def test_never_recovering_fault_reported():
    runner, _ = make_runner(break_at=5, heal_at=10**9)
    r = runner.run(CATALOGUE.get("S11"), TARGET)
    assert r.status == "not_recovered"


def test_aborts_if_unhealthy_before_injection():
    class Sick(ScriptedProbe):
        def scalar(self, q):
            return 1.0

    clock = FakeClock()
    probe = Sick(clock, 0, 0)
    ex = DryRunExecutor()
    r = Runner(CATALOGUE, probe, ex, clock=clock, sleep=clock.sleep).run(CATALOGUE.get("S03"), TARGET)
    assert r.status == "aborted"
    assert ex.calls == []  # nothing injected


def test_fault_reverted_even_if_monitoring_crashes():
    runner, ex = make_runner(10, 100)

    def boom():
        raise RuntimeError("prometheus down")
    runner.probe.firing_alerts = boom
    with pytest.raises(RuntimeError):
        runner.run(CATALOGUE.get("S05"), TARGET)
    assert any("iptables -D" in c[-1] for c in ex.calls)


# ------------------------------------------------------------------ report

def test_report_contains_measurements_and_followups():
    runner, _ = make_runner(break_at=10**9, heal_at=10**9 + 1)
    s = CATALOGUE.get("S06")
    md = report.run_report(s, runner.run(s, TARGET).to_dict())
    assert "Time to detect | —" in md
    assert "Observability gap" in md
    assert "KafkaConsumerLagHigh" in md


# --------------------------------------------------------------------- cli

def test_inventory_lookup(tmp_path):
    inv = tmp_path / "inventory.ini"
    inv.write_text(
        "[workers]\n"
        "infra-reliability-worker-2 ansible_host=198.51.100.9 private_ip=10.20.1.12\n"
        "[k8s:vars]\nansible_user=ubuntu\n"
    )
    assert resolve_target("infra-reliability-worker-2", inv).ssh_host == "198.51.100.9"


def test_cli_dry_run_all(capsys):
    assert main(["run", "--all", "--dry-run", "--inventory", "/nonexistent"]) == 0
    out = capsys.readouterr().out
    assert out.count("## S") == 12
