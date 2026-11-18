# Runbook — network failures

Applies to: `KafkaConsumerLagHigh`, `KafkaUnderReplicatedPartitions` caused by connectivity, faults S05–S07.

General triage:

```bash
# from the suspect node
ssh ubuntu@<node> 'sudo iptables -S | head -40; tc qdisc show; ip -s link'
# cross-node reachability from a debug pod
kubectl run netcheck --rm -it --image=nicolaka/netshoot -- bash
```

---

## S05 network-partition

**Symptoms:** node becomes `NotReady` (kubelet cannot reach the API server); the broker on that node falls out of the ISR; Spark master loses the worker heartbeat; Trino coordinator drops the worker.

**Actions:**
1. Check for stray DROP rules: `sudo iptables -S INPUT; sudo iptables -S OUTPUT`.
2. Check the AWS security group still allows intra-group traffic (`terraform plan` should show no drift).
3. Remove injected/stray rules (`sudo iptables -D ...`) or restore the SG with `terraform apply`.
4. After connectivity returns, Kafka replicas catch up automatically — watch under-replicated partitions fall to 0.

**Safety:** chaoskit installs a dead-man switch that deletes its rules `duration + 120s` after injection, even if the runner dies.

---

## S06 network-latency

**Symptoms:** no hard failure. Produce latency (acks=all waits for the slow replica) and consumer lag rise; Trino queries slow down.

**Actions:**
1. `tc qdisc show dev ens5` — remove any `netem` qdisc: `sudo tc qdisc del dev ens5 root netem`.
2. If latency is real (not injected), check instance network metrics in CloudWatch and ENA driver stats: `ethtool -S ens5 | grep -E 'exceeded|drop'`.

**Lesson:** latency is the classic "grey failure" — nodes stay Ready, so only lag/latency-based alerts catch it. If this scenario is reported `not_detected`, tighten `KafkaConsumerLagHigh` or add a produce-latency alert on `kafka_network_requestmetrics_totaltimems`.

---

## S07 packet-loss

**Symptoms:** TCP retransmits; intermittent ISR shrink/expand on Kafka; flapping Spark worker heartbeats.

**Actions:**
1. `tc qdisc show dev ens5`; remove netem as in S06.
2. `netstat -s | grep -i retrans` to confirm loss is gone.
3. If ISR keeps flapping after recovery, check `replica.lag.time.max.ms` and broker logs: `kubectl -n kafka logs events-dual-role-<n> | grep -i isr`.
