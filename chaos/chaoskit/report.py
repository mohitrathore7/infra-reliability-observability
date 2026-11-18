"""Render experiment results as Markdown: per-run RCA reports and a summary table."""
from __future__ import annotations

from .config import Scenario


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}s"


def run_report(s: Scenario, r: dict, k8s_events: str = "") -> str:
    """Post-incident write-up for one experiment.

    Measured facts (timeline, alerts) are filled in automatically; the
    root-cause and follow-up sections are left for the engineer to complete
    after reviewing the dashboard for the run window.
    """
    first_breaks = next((o for o in r["timeline"] if o["failing_checks"]), None)
    lines = [
        f"# {s.id} {s.name} — {r['started_at']}",
        "",
        f"**Fault:** {s.description}  ",
        f"**Target:** `{r['target_node']}`  ",
        f"**Result:** `{r['status']}`  ",
        f"**Runbook:** [{s.runbook}](../../{s.runbook})",
        "",
        "## Measurements",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Time to detect | {_fmt(r['time_to_detect_s'])} |",
        f"| Time to recover (from injection) | {_fmt(r['time_to_recover_s'])} |",
        f"| MTTR (from fault removal) | {_fmt(r['mttr_s'])} |",
        f"| Detected by | {', '.join(r['detected_by']) or '—'} |",
        f"| Alerts fired | {', '.join(r['alerts_seen']) or '—'} |",
        f"| Expected alerts that did NOT fire | {', '.join(r['expected_alerts_missing']) or 'none'} |",
        "",
        "## Timeline",
        "",
        "| t (s) | Failing steady-state checks | Firing alerts |",
        "|---:|---|---|",
    ]
    prev = None
    for o in r["timeline"]:
        key = (tuple(o["failing_checks"]), tuple(o["firing_alerts"]))
        if key != prev:  # only print state changes
            lines.append(f"| {o['t']:.0f} | {', '.join(o['failing_checks']) or 'healthy'} "
                         f"| {', '.join(o['firing_alerts']) or '—'} |")
            prev = key

    if first_breaks:
        symptom = f"{', '.join(first_breaks['failing_checks'])} at t={first_breaks['t']:.0f}s"
    else:
        symptom = "none observed"

    lines += [
        "",
        "## Root-cause analysis",
        "",
        f"- **Trigger:** injected fault — {s.description}",
        f"- **First observable symptom:** {symptom}",
        "- **Blast radius:** _which services degraded and for how long (see dashboard for the run window)_",
        "- **Why recovery took as long as it did:** _e.g. node grace period, eviction timeout, ISR catch-up_",
        "",
        "## Follow-ups",
        "",
    ]
    if r["expected_alerts_missing"]:
        lines.append(f"- [ ] Alerting gap: {', '.join(r['expected_alerts_missing'])} did not fire — "
                     "review thresholds / `for:` durations.")
    if r["status"] == "not_detected":
        lines.append("- [ ] Observability gap: no steady-state check or alert caught this fault.")
    if r["status"] == "not_recovered":
        lines.append("- [ ] Cluster did not self-heal within the timeout — follow the runbook manually.")
    lines.append("- [ ] _add follow-ups here_")

    for n in r.get("notes", []):
        lines.append(f"\n> note: {n}")

    if k8s_events:
        lines += ["", "## Kubernetes events during the run", "", "```", k8s_events.strip(), "```"]
    return "\n".join(lines) + "\n"


def summary(results: list[tuple[Scenario, dict]]) -> str:
    lines = [
        "# Failure-injection summary",
        "",
        "| ID | Scenario | Category | Status | Detect | Recover | MTTR | Missing alerts |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for s, r in results:
        lines.append(
            f"| {s.id} | {s.name} | {s.category} | {r['status']} | {_fmt(r['time_to_detect_s'])} "
            f"| {_fmt(r['time_to_recover_s'])} | {_fmt(r['mttr_s'])} "
            f"| {', '.join(r['expected_alerts_missing']) or '—'} |"
        )
    return "\n".join(lines) + "\n"
