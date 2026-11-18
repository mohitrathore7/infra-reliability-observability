# Runbook — node failures

Applies to: `NodeNotReady`, node-level faults S01–S04.

General triage:

```bash
kubectl get nodes -o wide
kubectl describe node <node> | sed -n '/Conditions/,/Addresses/p'
kubectl get pods -A -o wide --field-selector spec.nodeName=<node>
```

---

## S01 worker-node-stop

**Symptoms:** node `NotReady`; one Kafka broker gone (under-replicated partitions > 0); Spark alive workers 2/3; Trino active nodes drop.

**Expected self-healing:** after `node-monitor-grace-period` (40s) the node is marked NotReady; after the default 300s toleration pods are evicted. Spark/Trino worker Deployments reschedule onto the remaining nodes. Kafka brokers do **not** move (their PVCs are node-local) — the cluster runs on 2/3 replicas with `min.insync.replicas=2`, so produce with `acks=all` keeps working.

**Actions:**
1. Confirm the instance state: `aws ec2 describe-instances --filters Name=tag:Name,Values=<node> --query 'Reservations[].Instances[].State.Name'`
2. If stopped/impaired, start it: `aws ec2 start-instances --instance-ids <id>`
3. Wait for `Ready`, then confirm the broker rejoined the ISR: Grafana → *Kafka under-replicated partitions* returns to 0.
4. If the instance is unrecoverable, `kubectl delete node <node>`, recreate it with `terraform apply` + `ansible-playbook site.yml --limit <node>`; Strimzi will rebuild the broker from replicas.

**Verify:** all steady-state checks green (`python -m chaoskit run --dry-run` lists them; Grafana dashboard all panels nominal).

---

## S02 kubelet-stop

**Symptoms:** node `NotReady`, but containers on the node keep running (Kafka keeps serving), so data-plane impact is lower than S01.

**Actions:**
1. `ssh ubuntu@<node> 'sudo systemctl status kubelet; sudo journalctl -u kubelet -n 100'`
2. `sudo systemctl restart kubelet`
3. If kubelet crash-loops: check certificates (`/var/lib/kubelet/pki`), disk space (`df -h /`), and containerd health (S03).

**Verify:** node `Ready`; no pods stuck `Terminating` (`kubectl get pods -A | grep Terminating`).

---

## S03 containerd-restart

**Symptoms:** short burst of container restarts on one node; pod restart panel spikes; `PodRestartLoop` may fire if restarts repeat.

**Actions:**
1. `ssh ubuntu@<node> 'sudo systemctl status containerd; sudo journalctl -u containerd -n 100'`
2. Check pods recovered: `kubectl get pods -A -o wide | grep <node>`
3. If restarts continue, look for a config regression: `SystemdCgroup = true` must be set in `/etc/containerd/config.toml` (re-run the `containerd` Ansible role).

---

## S04 node-drain

**Symptoms:** planned — pods evicted from one node; Kafka broker on that node is evicted and stays `Pending` (its PVC is pinned to the node), so partitions are under-replicated until uncordon.

**Actions:**
1. Before draining in production, confirm PodDisruptionBudgets allow it: `kubectl get pdb -A`.
2. After maintenance: `kubectl uncordon <node>`.
3. Watch the broker return: `kubectl -n kafka get pods -w`.

**Lesson:** node-local storage means a drained node takes a Kafka replica with it. Keep drains short or use network-attached volumes.
