"""Fault injection actions.

Every action is a pair of shell commands: `inject` and an optional `revert`.
Node-level faults run over SSH; cluster-level faults use kubectl; instance
stop/start uses the AWS CLI. Commands are built as argument lists, never
interpolated into a local shell, and an Executor runs them so tests (and
--dry-run) can substitute a recorder.

Network faults also install a dead-man switch on the node that removes the
fault after `duration + 120s` even if the runner dies mid-experiment.
"""
from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .config import Scenario

DEADMAN_GRACE_S = 120


class Executor(Protocol):
    def run(self, argv: list[str]) -> str: ...


class ShellExecutor:
    def run(self, argv: list[str]) -> str:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"{argv[0]} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout


class DryRunExecutor:
    def __init__(self):
        self.calls: list[list[str]] = []

    def run(self, argv: list[str]) -> str:
        self.calls.append(argv)
        return ""


@dataclass
class Plan:
    inject: list[list[str]]
    revert: list[list[str]]


@dataclass
class Target:
    node: str        # Kubernetes node name
    ssh_host: str    # public IP / hostname for SSH
    ssh_user: str = "ubuntu"


def ssh(t: Target, remote_cmd: str) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            f"{t.ssh_user}@{t.ssh_host}", remote_cmd]


def deadman(remote_revert: str, delay_s: int) -> str:
    return f"sudo nohup sh -c {shlex.quote(f'sleep {delay_s}; {remote_revert}')} >/dev/null 2>&1 &"


# --------------------------------------------------------------- builders

def _ec2_stop_start(s: Scenario, t: Target) -> Plan:
    find = ["aws", "ec2", "describe-instances",
            "--filters", f"Name=tag:Name,Values={t.node}", "Name=instance-state-name,Values=running,stopped",
            "--query", "Reservations[0].Instances[0].InstanceId", "--output", "text"]
    # instance id resolved at runtime by the runner via `find`
    return Plan(
        inject=[find, ["aws", "ec2", "stop-instances", "--instance-ids", "{instance_id}"]],
        revert=[["aws", "ec2", "start-instances", "--instance-ids", "{instance_id}"]],
    )


def _systemd_stop_start(s: Scenario, t: Target) -> Plan:
    unit = shlex.quote(s.params["unit"])
    return Plan(
        inject=[ssh(t, f"sudo systemctl stop {unit}")],
        revert=[ssh(t, f"sudo systemctl start {unit}")],
    )


def _systemd_restart(s: Scenario, t: Target) -> Plan:
    unit = shlex.quote(s.params["unit"])
    return Plan(inject=[ssh(t, f"sudo systemctl restart {unit}")], revert=[])


def _kubectl_drain_uncordon(s: Scenario, t: Target) -> Plan:
    return Plan(
        inject=[["kubectl", "drain", t.node, "--ignore-daemonsets",
                 "--delete-emptydir-data", "--timeout=180s"]],
        revert=[["kubectl", "uncordon", t.node]],
    )


def _iptables_partition(s: Scenario, t: Target) -> Plan:
    cidr = shlex.quote(s.params["cidr"])
    add = (f"sudo iptables -I INPUT -s {cidr} -j DROP && "
           f"sudo iptables -I OUTPUT -d {cidr} -j DROP")
    rm = (f"iptables -D INPUT -s {cidr} -j DROP; "
          f"iptables -D OUTPUT -d {cidr} -j DROP")
    return Plan(
        inject=[ssh(t, deadman(rm, s.duration_s + DEADMAN_GRACE_S)), ssh(t, add)],
        revert=[ssh(t, f"sudo sh -c {shlex.quote(rm)}")],
    )


def _tc_netem(s: Scenario, t: Target) -> Plan:
    dev = shlex.quote(s.params["interface"])
    netem = s.params["netem"]
    rm = f"tc qdisc del dev {dev} root netem"
    return Plan(
        inject=[ssh(t, deadman(rm, s.duration_s + DEADMAN_GRACE_S)),
                ssh(t, f"sudo tc qdisc add dev {dev} root netem {netem}")],
        revert=[ssh(t, f"sudo {rm}")],
    )


def _stress_ng(s: Scenario, t: Target) -> Plan:
    # stress-ng stops itself after --timeout; revert only kills stragglers.
    return Plan(
        inject=[ssh(t, f"nohup stress-ng {s.params['args']} --timeout {s.duration_s}s "
                       ">/dev/null 2>&1 &")],
        revert=[ssh(t, "pkill -f stress-ng || true")],
    )


def _disk_fill(s: Scenario, t: Target) -> Plan:
    path = shlex.quote(s.params["path"])
    frac = float(s.params["fraction"])
    size = f"$(df --output=avail -B1 / | tail -1 | awk '{{printf \"%d\", $1*{frac}}}')"
    return Plan(
        inject=[ssh(t, deadman(f"rm -f {path}", s.duration_s + DEADMAN_GRACE_S)),
                ssh(t, f"sudo fallocate -l {size} {path}")],
        revert=[ssh(t, f"sudo rm -f {path}")],
    )


def _pod_kill(s: Scenario, t: Target) -> Plan:
    targets = s.params.get("targets") or [s.params]
    inject = []
    for tg in targets:
        # pick the first matching pod; runner substitutes the name
        inject.append(["kubectl", "-n", tg["namespace"], "get", "pods", "-l", tg["selector"],
                       "-o", "jsonpath={.items[0].metadata.name}"])
        inject.append(["kubectl", "-n", tg["namespace"], "delete", "pod", "{last_output}",
                       "--grace-period=0", "--force"])
    return Plan(inject=inject, revert=[])


BUILDERS: dict[str, Callable[[Scenario, Target], Plan]] = {
    "ec2_stop_start": _ec2_stop_start,
    "systemd_stop_start": _systemd_stop_start,
    "systemd_restart": _systemd_restart,
    "kubectl_drain_uncordon": _kubectl_drain_uncordon,
    "iptables_partition": _iptables_partition,
    "tc_netem": _tc_netem,
    "stress_ng": _stress_ng,
    "disk_fill": _disk_fill,
    "pod_kill": _pod_kill,
}


def build_plan(s: Scenario, t: Target) -> Plan:
    try:
        return BUILDERS[s.action](s, t)
    except KeyError:
        raise ValueError(f"{s.id}: unknown action {s.action!r}") from None


def execute(cmds: list[list[str]], ex: Executor, ctx: dict[str, str]) -> None:
    """Run commands in order. `{last_output}` is replaced by the previous
    command's stdout; `{instance_id}` is captured from the first lookup."""
    last = ""
    for argv in cmds:
        resolved = []
        for a in argv:
            a = a.replace("{last_output}", last)
            for key, val in ctx.items():
                a = a.replace("{" + key + "}", val)
            resolved.append(a)
        out = ex.run(resolved).strip()
        if argv[:3] == ["aws", "ec2", "describe-instances"]:
            ctx["instance_id"] = out
        last = out
