#!/usr/bin/env bash
# Build the Kube-Sentinel image into minikube and install it with Helm.
# Prerequisites: monitoring/install.sh and rag/install.sh have run; GOOGLE_API_KEY is in .env.
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="$(grep '^appVersion:' deploy/helm/kube-sentinel/Chart.yaml | cut -d'"' -f2)"

echo "Building kube-sentinel:${TAG} inside minikube..."
minikube image build -t "kube-sentinel:${TAG}" .

# Gemini key -> Kubernetes Secret, read from .env without echoing it.
KEY="$(grep '^GOOGLE_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r"'"'"' ')"
if [ -z "${KEY}" ]; then echo "GOOGLE_API_KEY missing in .env" >&2; exit 1; fi
kubectl -n kube-sentinel create secret generic kube-sentinel-gemini \
  --from-literal=GOOGLE_API_KEY="${KEY}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset KEY

helm upgrade --install kube-sentinel deploy/helm/kube-sentinel -n kube-sentinel --wait --timeout 5m
