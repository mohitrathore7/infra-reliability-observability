# Runbook — service failures (Kafka, Spark, Trino)

Applies to: `KafkaUnderReplicatedPartitions`, `KafkaOfflinePartitions`, `SparkWorkersMissing`, `TrinoWorkersMissing`, `TrinoQueryFailures`, `PodRestartLoop`, faults S11–S12.

General triage:

```bash
kubectl get pods -n kafka -n spark -n trino -o wide
kubectl describe pod -n <ns> <pod>
kubectl logs -n <ns> <pod> --previous
```

---

## S11 kafka-broker-kill

**Symptoms:** one broker restarts; partitions it led fail over to other replicas; under-replicated partitions > 0 until the broker restarts and catches up.

**Expected self-healing:** StatefulSet-style recreation by Strimzi on the same node (PVC is local); broker rejoins the ISR after log recovery.

**Actions if it does not recover:**
1. `kubectl -n kafka get pods -l strimzi.io/name=events-kafka`
2. Pending? → node or PVC problem: `kubectl -n kafka describe pod <pod>`.
3. CrashLoop? → `kubectl -n kafka logs <pod> --previous` (look for corrupted log segments or disk full — see S10).
4. Check `KafkaOfflinePartitions` — if > 0, data is unavailable; do **not** enable unclean leader election without sign-off.

---

## S12 spark-and-trino-worker-kill

**Symptoms:** Spark alive workers drop to 2 (running apps lose executors and reschedule tasks); Trino active nodes drop and in-flight queries on that worker fail (`TrinoQueryFailures`).

**Expected self-healing:** Deployments recreate both pods within seconds; Spark worker re-registers with the master; Trino worker re-announces to the coordinator.

**Actions if it does not recover:**
1. Spark: `kubectl -n spark logs deploy/spark-master | grep -i worker` — check registration; verify `spark-master` Service resolves.
2. Trino: `kubectl -n trino logs <worker>` — look for discovery URI / memory config errors.
3. Re-run failed Trino queries; Trino does not retry by default (consider `retry-policy=QUERY` for batch workloads).
