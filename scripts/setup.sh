#!/usr/bin/env bash
# Deploys the Online Boutique demo shop (the app Kube-Sentinel monitors).
set -euo pipefail

BOUTIQUE_VERSION="v0.10.7"
MANIFEST_URL="https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/${BOUTIQUE_VERSION}/release/kubernetes-manifests.yaml"

echo "Deploying Online Boutique ${BOUTIQUE_VERSION}..."
kubectl apply -f "${MANIFEST_URL}"

echo "Waiting for all pods to be ready (first run can take ~5 minutes)..."
kubectl wait --for=condition=available deployment --all --timeout=600s

echo
echo "Done. Open the shop with:"
echo "  kubectl port-forward svc/frontend 8080:80"
echo "then visit http://localhost:8080"
