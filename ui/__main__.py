"""Start the approval UI.

    python -m ui              # http://127.0.0.1:8000
    python -m ui --watch      # also investigate automatically when shop alerts fire
    python -m ui --port 8100

In the cluster (container image) it runs with KUBE_SENTINEL_UI_HOST=0.0.0.0 so the Kubernetes Service
can reach it, and KUBE_SENTINEL_INGEST_ON_START=1 so the runbooks in the image are loaded first.
"""

import os
import subprocess
import sys

import uvicorn


def _arg(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    if "--watch" in sys.argv:
        os.environ["KUBE_SENTINEL_UI_WATCH"] = "1"
    port = int(_arg("--port", os.environ.get("KUBE_SENTINEL_UI_PORT", "8000")))
    # 127.0.0.1 by default: this app can change the cluster, so on a laptop it must not be reachable
    # from other machines. Inside a pod, 0.0.0.0 is needed for the (ClusterIP-only) Service.
    host = _arg("--host", os.environ.get("KUBE_SENTINEL_UI_HOST", "127.0.0.1"))

    if os.environ.get("KUBE_SENTINEL_INGEST_ON_START") == "1":
        # Separate short-lived process: the embedding model's memory is freed when it exits,
        # instead of staying resident in the web server for the pod's whole life.
        subprocess.run([sys.executable, "-m", "rag.ingest"], check=True)

    watching = os.environ.get("KUBE_SENTINEL_UI_WATCH") == "1"
    print(f"Kube-Sentinel approval UI: http://{host}:{port}" + ("  (watching Alertmanager)" if watching else ""),
          flush=True)
    uvicorn.run("ui.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
