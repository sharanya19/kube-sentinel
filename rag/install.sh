#!/usr/bin/env bash
# Deploys Postgres + pgvector for runbook search, then loads the runbooks into it.
set -euo pipefail
cd "$(dirname "$0")/.."

kubectl apply -f rag/k8s/postgres.yaml >/dev/null

# Random password, created once and kept in a Kubernetes Secret (never in the repo).
if ! kubectl -n kube-sentinel get secret runbooks-db >/dev/null 2>&1; then
  kubectl -n kube-sentinel create secret generic runbooks-db \
    --from-literal=password="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi

echo "Waiting for Postgres..."
kubectl -n kube-sentinel rollout status statefulset/runbooks-db --timeout=300s

echo "Loading runbooks (first run downloads a ~130 MB embedding model)..."
python -m rag.ingest
