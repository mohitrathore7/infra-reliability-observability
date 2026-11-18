#!/usr/bin/env bash
# Deploys storage, monitoring, Kafka, Spark and Trino onto the cluster.
# Usage: KUBECONFIG=./kubeconfig GRAFANA_ADMIN_PASSWORD=... ./scripts/deploy-stack.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}"

LOCAL_PATH_VERSION="v0.0.28"
STRIMZI_VERSION="0.41.0"

echo "==> Namespaces"
kubectl apply -f "$ROOT/k8s/namespaces.yaml"

echo "==> Default StorageClass (local-path-provisioner)"
kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"
kubectl patch storageclass local-path \
  -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

echo "==> Helm repos"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add strimzi https://strimzi.io/charts/ >/dev/null
helm repo add trino https://trinodb.github.io/charts >/dev/null
helm repo update >/dev/null

echo "==> Prometheus + Grafana"
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f "$ROOT/monitoring/kube-prometheus-stack-values.yaml" \
  --set grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" --wait --timeout 10m

python3 "$ROOT/scripts/build_dashboard.py" > "$ROOT/monitoring/dashboards/cluster-reliability.json"
kubectl -n monitoring create configmap reliability-dashboard \
  --from-file="$ROOT/monitoring/dashboards/cluster-reliability.json" \
  --dry-run=client -o yaml | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -

echo "==> Kafka (Strimzi operator + 3-broker KRaft cluster)"
helm upgrade --install strimzi strimzi/strimzi-kafka-operator \
  -n kafka --version "$STRIMZI_VERSION" --set watchNamespaces="{kafka}" --wait
kubectl apply -f "$ROOT/k8s/kafka/kafka-metrics.yaml"
kubectl apply -f "$ROOT/k8s/kafka/kafka-cluster.yaml"
kubectl -n kafka wait kafka/events --for=condition=Ready --timeout=15m
kubectl apply -f "$ROOT/k8s/kafka/load-generator.yaml"

echo "==> Spark standalone"
kubectl apply -f "$ROOT/k8s/spark/spark-standalone.yaml"
kubectl -n spark rollout status deploy/spark-master --timeout=5m
kubectl -n spark rollout status deploy/spark-worker --timeout=5m
kubectl apply -f "$ROOT/k8s/spark/spark-batch-job.yaml"

echo "==> Trino"
helm upgrade --install trino trino/trino -n trino -f "$ROOT/k8s/trino/values.yaml" --wait --timeout 10m
kubectl apply -f "$ROOT/k8s/trino/query-load.yaml"

echo "==> Scrape targets + alert rules"
kubectl apply -f "$ROOT/monitoring/scrape-targets.yaml"
kubectl apply -f "$ROOT/monitoring/rules/reliability-alerts.yaml"

CP_IP="$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')"
echo
echo "Done. Grafana:    http://${CP_IP:-<control-plane-ip>}:30300  (admin / \$GRAFANA_ADMIN_PASSWORD)"
echo "      Prometheus: http://${CP_IP:-<control-plane-ip>}:30090"
