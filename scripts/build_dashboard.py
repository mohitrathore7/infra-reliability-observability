#!/usr/bin/env python3
"""Generate the Grafana dashboard JSON from a single list of panel definitions.

Keeping the dashboard as code means the 12 monitored metrics are reviewed in
one place and the JSON never drifts from what the alert rules and the chaos
runner's steady-state checks use.

    python scripts/build_dashboard.py > monitoring/dashboards/cluster-reliability.json
"""
import json
import sys

# (title, unit, [(promql, legend)])  -- 12 metrics, 6 infrastructure + 6 application
PANELS = [
    # ---- infrastructure ----
    ("Node CPU utilisation", "percent",
     [('100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)', "{{instance}}")]),
    ("Node memory utilisation", "percent",
     [("(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100", "{{instance}}")]),
    ("Root disk usage", "percent",
     [('(1 - node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} '
       '/ node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}) * 100', "{{instance}}")]),
    ("Network receive throughput", "Bps",
     [('sum by (instance) (rate(node_network_receive_bytes_total{device!~"lo|cali.*|tunl.*|vxlan.*"}[5m]))',
       "{{instance}}")]),
    ("Ready nodes", "short",
     [('sum(kube_node_status_condition{condition="Ready",status="true"})', "ready")]),
    ("Pod restarts (15m)", "short",
     [('sum by (namespace) (increase(kube_pod_container_status_restarts_total{namespace=~"kafka|spark|trino"}[15m]))',
       "{{namespace}}")]),
    # ---- application ----
    ("Kafka under-replicated partitions", "short",
     [("sum(kafka_server_replicamanager_underreplicatedpartitions)", "URP")]),
    ("Kafka consumer lag", "short",
     [("sum by (consumergroup) (kafka_consumergroup_lag)", "{{consumergroup}}")]),
    ("Kafka messages in / s", "short",
     [('sum(rate(kafka_server_brokertopicmetrics_messagesin_total{topic!=""}[1m]))', "msg/s")]),
    ("Spark alive workers", "short",
     [("max(metrics_master_aliveWorkers_Value)", "alive workers")]),
    ("Spark running / waiting apps", "short",
     [("max(metrics_master_apps_Value)", "running"),
      ("max(metrics_master_waitingApps_Value)", "waiting")]),
    ("Trino active nodes & running queries", "short",
     [("max(trino_metadata_name_DiscoveryNodeManager_ActiveNodeCount)", "active nodes"),
      ("sum(trino_execution_name_QueryManager_RunningQueries)", "running queries")]),
]


def panel(idx, title, unit, targets):
    return {
        "id": idx + 1,
        "type": "timeseries",
        "title": title,
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
        "gridPos": {"h": 8, "w": 8, "x": (idx % 3) * 8, "y": (idx // 3) * 8},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
        "targets": [
            {"refId": chr(65 + i), "expr": expr, "legendFormat": legend}
            for i, (expr, legend) in enumerate(targets)
        ],
    }


def build():
    return {
        "uid": "infra-reliability",
        "title": "Infrastructure Reliability Overview",
        "tags": ["reliability", "kafka", "spark", "trino"],
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "15s",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {"list": [{
            "name": "datasource", "type": "datasource", "query": "prometheus",
            "current": {"text": "Prometheus", "value": "prometheus"},
        }]},
        "panels": [panel(i, *p) for i, p in enumerate(PANELS)],
    }


if __name__ == "__main__":
    assert len(PANELS) == 12, "dashboard must track exactly 12 metrics"
    json.dump(build(), sys.stdout, indent=2)
    sys.stdout.write("\n")
