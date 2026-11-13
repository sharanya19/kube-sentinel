# Scripts

## Overview
Cross-platform cluster bootstrap scripts ([setup.sh](./setup.sh) for Bash/POSIX and [setup.ps1](./setup.ps1) for Windows PowerShell) that deploy Google Cloud's Online Boutique demo application (`microservices-demo`) into the active Kubernetes cluster context as the target workload monitored by Kube-Sentinel.

## Interface
- [setup.sh](./setup.sh) — Bash bootstrap script setting `BOUTIQUE_VERSION="v0.10.7"` and `MANIFEST_URL`, applying the upstream release manifest via `kubectl apply -f`, and waiting for all deployments to become available `[setup.sh:BOUTIQUE_VERSION]`, `[setup.sh:MANIFEST_URL]`, `[setup.sh:kubectl apply]`, `[setup.sh:kubectl wait]`.
- [setup.ps1](./setup.ps1) — PowerShell bootstrap script setting `$BoutiqueVersion = "v0.10.7"` and `$ManifestUrl`, applying the upstream release manifest via `kubectl apply -f`, and waiting for all deployments to become available `[setup.ps1:$BoutiqueVersion]`, `[setup.ps1:$ManifestUrl]`, `[setup.ps1:kubectl apply]`, `[setup.ps1:kubectl wait]`.
- Subdirectories: None.

## Invariants
- Both scripts enforce fail-fast execution (`set -euo pipefail` in `setup.sh`; `$ErrorActionPreference = "Stop"` in `setup.ps1`) `[setup.sh:set]`, `[setup.ps1:$ErrorActionPreference]`.
- Both scripts pin identical Online Boutique version `v0.10.7` (`https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/v0.10.7/release/kubernetes-manifests.yaml`) and block up to `600s` (`--timeout=600s`) until `condition=available` holds across `--all` deployments `[setup.sh:BOUTIQUE_VERSION]`, `[setup.ps1:$BoutiqueVersion]`, `[setup.sh:kubectl wait]`, `[setup.ps1:kubectl wait]`.

## Side Effects
- Downloads the `v0.10.7` `kubernetes-manifests.yaml` bundle over HTTPS from `raw.githubusercontent.com` and creates or updates Online Boutique `Deployment`, `Service`, and `ServiceAccount` objects in the current `kubectl` namespace context `[setup.sh:MANIFEST_URL]`, `[setup.ps1:$ManifestUrl]`, `[setup.sh:kubectl apply]`, `[setup.ps1:kubectl apply]`.
- Polls the Kubernetes API via `kubectl wait --for=condition=available deployment --all --timeout=600s` until all deployments are ready `[setup.sh:kubectl wait]`, `[setup.ps1:kubectl wait]`.

## Verification
- Run: `<test-command>`
