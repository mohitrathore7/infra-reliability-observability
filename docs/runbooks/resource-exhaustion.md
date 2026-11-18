# Runbook — resource exhaustion

Applies to: `NodeHighCPU`, `NodeMemoryPressure`, `NodeDiskAlmostFull`, faults S08–S10.

General triage:

```bash
kubectl top nodes
kubectl top pods -A --sort-by=cpu | head -20
ssh ubuntu@<node> 'uptime; free -m; df -h /; ps aux --sort=-%cpu | head'
```

---

## S08 cpu-saturation

**Symptoms:** CPU panel at ~100% on one node; Spark tasks on that node slow down (stragglers); Kafka request latency rises. Pods are not evicted — CPU is compressible.

**Actions:**
1. Identify the consumer: `ps aux --sort=-%cpu | head`. If it is not a Kubernetes workload, stop it.
2. If it is a pod without CPU limits, set requests/limits so the scheduler can account for it.
3. Short-term relief: `kubectl cordon <node>` to stop new work landing there.

---

## S09 memory-pressure

**Symptoms:** `MemoryPressure=True` on the node; kubelet evicts BestEffort/Burstable pods; possible OOM kills (`dmesg | grep -i oom`).

**Actions:**
1. `kubectl describe node <node> | grep -A5 Conditions`
2. `kubectl get events -A --field-selector reason=Evicted`
3. Kill/limit the offending process; evicted pods are recreated by their controllers.
4. Make sure Kafka/Spark/Trino pods have `requests == limits` for memory (Guaranteed QoS) so they are evicted last — already set in this repo's manifests.

---

## S10 disk-fill

**Symptoms:** root filesystem > 85%; at ~90% kubelet reports `DiskPressure` and starts image garbage collection and pod eviction. Kafka log segments live on this disk (local-path) — a full disk stops the broker.

**Actions:**
1. Find the space: `sudo du -xh / --max-depth=2 2>/dev/null | sort -h | tail -20`
2. Remove the culprit (chaoskit's file is `/var/tmp/chaos-fill`).
3. Reclaim images if needed: `sudo crictl rmi --prune`.
4. If Kafka hit the limit, check the broker restarted cleanly and that topic retention is sane (`retention.ms` on `telemetry` is 24h).
