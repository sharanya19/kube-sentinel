#!/usr/bin/env bash
# Installs the monitoring stack into the "monitoring" namespace.
set -euo pipefail
cd "$(dirname "$0")"

KPS_VERSION="93.2.0"     # prometheus-community/kube-prometheus-stack
LOKI_VERSION="21.2.1"    # grafana-community/loki
ALLOY_VERSION="1.15.0"   # grafana/alloy

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm repo update

echo "Installing Prometheus + Alertmanager + Grafana..."
helm upgrade --install kps prometheus-community/kube-prometheus-stack \
  --version "$KPS_VERSION" -n monitoring --create-namespace \
  -f prometheus-values.yaml --wait --timeout 10m

echo "Installing Loki..."
helm upgrade --install loki grafana-community/loki \
  --version "$LOKI_VERSION" -n monitoring \
  -f loki-values.yaml --wait --timeout 10m

echo "Installing Alloy (log collector)..."
helm upgrade --install alloy grafana/alloy \
  --version "$ALLOY_VERSION" -n monitoring \
  -f alloy-values.yaml --wait --timeout 5m

echo "Adding Kube-Sentinel alert rules (metrics + logs)..."
kubectl apply -f alert-rules.yaml -f loki-rules.yaml

echo "Adding the Kube-Sentinel / Shop overview dashboard to Grafana..."
kubectl -n monitoring create configmap kube-sentinel-dashboards \
  --from-file=shop-overview.json=dashboards/shop-overview.json --dry-run=client -o yaml \
  | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -

echo
echo "Done. Open Grafana with:"
echo "  kubectl port-forward -n monitoring svc/kps-grafana 3000:80"
echo "then visit http://localhost:3000  (login: admin / kube-sentinel)"
