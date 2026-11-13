"""Watch Alertmanager and investigate automatically when shop alerts fire.

    python -m agent.watch            # poll forever
    python -m agent.watch --once     # investigate whatever is firing now, then exit

The agent pulls alerts from Alertmanager through the Kubernetes API proxy, so nothing on this
machine has to accept connections from the cluster. One incident usually fires several alerts;
the watcher waits briefly so they arrive together, then runs ONE investigation for the group.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from mcp_server import observability as obs

from .run import check_api_key, run_investigation

POLL_SECONDS = int(os.environ.get("KUBE_SENTINEL_POLL_SECONDS", "20"))
SETTLE_SECONDS = int(os.environ.get("KUBE_SENTINEL_SETTLE_SECONDS", "45"))
NAMESPACE = os.environ.get("KUBE_SENTINEL_NAMESPACE", "default")


def shop_alerts() -> list[dict]:
    return [a for a in obs.firing_alerts() if a.get("namespace") == NAMESPACE]


def alert_text(alerts: list[dict]) -> str:
    lines = [f"Alertmanager: {len(alerts)} alert(s) firing in namespace {NAMESPACE}:"]
    for a in sorted(alerts, key=lambda a: (a.get("severity") != "critical", a["alert"])):
        target = f" ({a['target']})" if a.get("target") else ""
        lines.append(f"- [{a.get('severity')}] {a['alert']}{target}: {a.get('summary')} since {a.get('since')}")
    lines.append("Find the root cause and suggest a fix.")
    return "\n".join(lines)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def investigate_now(alerts: list[dict]) -> None:
    text = alert_text(alerts)
    log("Investigating:\n" + text)
    try:
        result = await run_investigation(
            text, source="alertmanager",
            on_switch=lambda f, n, why: log(f"{f}: {why}" + (f"; switching to {n}" if n else "")),
            on_tool=lambda n, a: print(f"    -> {n}({json.dumps(a) if a else ''})", flush=True))
    except Exception as e:
        from .models import explain_error
        log(f"Investigation failed: {explain_error(e)}")
        return
    r = result["report"]
    log(f"Report {result['id']} ({result['tool_calls']} tool calls, {result['seconds']}s)\n"
        f"{r.to_text()}\n    saved as incident {result['id']}")


async def watch(once: bool = False) -> None:
    check_api_key()
    seen: set[str] = set()
    log(f"Watching Alertmanager for alerts in namespace '{NAMESPACE}' (every {POLL_SECONDS}s)...")
    while True:
        try:
            alerts = shop_alerts()
        except Exception as e:  # monitoring hiccup: keep watching
            log(f"Could not read alerts: {e}")
            alerts = []
        current = {a["fingerprint"] for a in alerts}
        if once:
            if alerts:
                await investigate_now(alerts)
            else:
                log("No shop alerts firing.")
            return
        if current - seen:
            log(f"{len(current - seen)} new alert(s); waiting {SETTLE_SECONDS}s for related alerts...")
            await asyncio.sleep(SETTLE_SECONDS)
            alerts = shop_alerts() or alerts
            await investigate_now(alerts)
            seen |= {a["fingerprint"] for a in alerts}
        elif seen and not current:
            log("All shop alerts resolved.")
            seen.clear()
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(watch(once="--once" in sys.argv))
    except KeyboardInterrupt:
        pass
