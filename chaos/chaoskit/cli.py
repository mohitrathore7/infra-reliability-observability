"""Command-line entry point.

    python -m chaoskit list
    python -m chaoskit run S05 --prometheus http://<cp-ip>:30090
    python -m chaoskit run --all --prometheus http://<cp-ip>:30090
    python -m chaoskit run S05 --dry-run          # print commands only
    python -m chaoskit summary                    # rebuild results/SUMMARY.md
"""
from __future__ import annotations

import argparse
import configparser
import json
import subprocess
import sys
from pathlib import Path

from . import report
from .actions import ShellExecutor, Target, build_plan
from .config import load
from .prometheus import Prometheus
from .runner import Runner

HERE = Path(__file__).resolve().parent.parent
DEFAULT_SCENARIOS = HERE / "scenarios.yaml"
DEFAULT_INVENTORY = HERE.parent / "ansible" / "inventory.ini"
RESULTS = HERE / "results"


def resolve_target(node: str, inventory: Path) -> Target:
    """Map a node name to its SSH address using the Terraform-generated inventory."""
    if not inventory.exists():
        return Target(node=node, ssh_host=node)
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=(" ",))
    parser.optionxform = str
    parser.read(inventory)
    for section in parser.sections():
        for key, val in parser.items(section):
            if key == node and val:
                fields = dict(kv.split("=", 1) for kv in val.split() if "=" in kv)
                return Target(node=node, ssh_host=fields.get("ansible_host", node))
    return Target(node=node, ssh_host=node)


def k8s_events() -> str:
    try:
        return subprocess.run(
            ["kubectl", "get", "events", "-A", "--sort-by=.lastTimestamp"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout[-6000:]
    except (OSError, subprocess.SubprocessError):
        return ""


def cmd_list(cat, _args) -> int:
    for s in cat.scenarios:
        print(f"{s.id}  {s.category:<9} {s.name:<28} {s.description}")
    return 0


def cmd_run(cat, args) -> int:
    chosen = cat.scenarios if args.all else [cat.get(i) for i in args.ids]
    if not chosen:
        print("give scenario ids or --all", file=sys.stderr)
        return 2

    if args.dry_run:
        for s in chosen:
            t = resolve_target(args.node or s.target_node, args.inventory)
            plan = build_plan(s, t)
            print(f"## {s.id} {s.name} -> {t.node} ({t.ssh_host})")
            for c in plan.inject:
                print("  inject:", " ".join(c))
            for c in plan.revert:
                print("  revert:", " ".join(c))
        return 0

    if not args.prometheus:
        print("--prometheus URL is required for a real run", file=sys.stderr)
        return 2

    runner = Runner(cat, Prometheus(args.prometheus), ShellExecutor())
    RESULTS.mkdir(exist_ok=True)
    failures = 0
    for s in chosen:
        t = resolve_target(args.node or s.target_node, args.inventory)
        print(f"==> {s.id} {s.name} on {t.node}", flush=True)
        r = runner.run(s, t).to_dict()
        stem = f"{r['started_at'].replace(':', '')}-{s.id}"
        (RESULTS / f"{stem}.json").write_text(json.dumps(r, indent=2))
        (RESULTS / f"{stem}.md").write_text(report.run_report(s, r, k8s_events()))
        print(f"    {r['status']}: detect={r['time_to_detect_s']}s "
              f"recover={r['time_to_recover_s']}s mttr={r['mttr_s']}s")
        failures += r["status"] != "passed"
    cmd_summary(cat, args)
    return 1 if failures else 0


def cmd_summary(cat, _args) -> int:
    latest: dict[str, dict] = {}
    for f in sorted(RESULTS.glob("*.json")):
        r = json.loads(f.read_text())
        latest[r["scenario_id"]] = r  # files sort by timestamp -> keep newest
    rows = [(cat.get(sid), r) for sid, r in sorted(latest.items())]
    if rows:
        (RESULTS / "SUMMARY.md").write_text(report.summary(rows))
        print(f"wrote {RESULTS / 'SUMMARY.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chaoskit")
    p.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("ids", nargs="*")
    run.add_argument("--all", action="store_true")
    run.add_argument("--node", help="override the target node")
    run.add_argument("--prometheus", help="e.g. http://<control-plane-ip>:30090")
    run.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("summary")

    args = p.parse_args(argv)
    cat = load(args.scenarios)
    return {"list": cmd_list, "run": cmd_run, "summary": cmd_summary}[args.cmd](cat, args)
