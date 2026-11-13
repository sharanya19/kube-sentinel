"""Start the approval UI.

    python -m ui              # http://127.0.0.1:8000
    python -m ui --watch      # also investigate automatically when shop alerts fire
    python -m ui --port 8100
"""

import os
import sys

import uvicorn


def main() -> None:
    if "--watch" in sys.argv:
        os.environ["KUBE_SENTINEL_UI_WATCH"] = "1"
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    print(f"Kube-Sentinel approval UI: http://127.0.0.1:{port}"
          + ("  (watching Alertmanager)" if "--watch" in sys.argv else ""))
    # 127.0.0.1 only: this app can change the cluster, so it must not be reachable from other machines.
    uvicorn.run("ui.server:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
