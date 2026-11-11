# Deploys the Online Boutique demo shop (the app Kube-Sentinel monitors).
$ErrorActionPreference = "Stop"

$BoutiqueVersion = "v0.10.7"
$ManifestUrl = "https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/$BoutiqueVersion/release/kubernetes-manifests.yaml"

Write-Host "Deploying Online Boutique $BoutiqueVersion..."
kubectl apply -f $ManifestUrl

Write-Host "Waiting for all pods to be ready (first run can take ~5 minutes)..."
kubectl wait --for=condition=available deployment --all --timeout=600s

Write-Host ""
Write-Host "Done. Open the shop with:"
Write-Host "  kubectl port-forward svc/frontend 8080:80"
Write-Host "then visit http://localhost:8080"
