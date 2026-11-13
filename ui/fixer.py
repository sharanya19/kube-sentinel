"""Run an approved fix through the MCP server, then check that the cluster recovered.

This is the ONLY place in Kube-Sentinel that changes the cluster. It starts the MCP server with
fix actions enabled (KUBE_SENTINEL_ALLOW_ACTIONS=true), so every change still goes through the
server's allowlist: three actions, allowed namespaces only, replicas capped.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server import k8s

ACTIONS = {"scale_deployment", "rollout_undo", "rollout_restart"}
VERIFY_TIMEOUT = 240  # seconds to wait for the deployment to become healthy


def _args(fix: dict[str, Any], dry_run: bool) -> tuple[str, dict[str, Any]]:
    action = fix.get("action")
    if action not in ACTIONS:
        raise ValueError(f"'{action}' is not an allowed fix action")
    if not fix.get("deployment"):
        raise ValueError("The suggested fix names no deployment")
    args: dict[str, Any] = {"name": fix["deployment"], "namespace": "default", "dry_run": dry_run}
    if action == "scale_deployment":
        args["replicas"] = int(fix.get("replicas") or 1)
    return action, args


async def call_action(fix: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Call one fix action over MCP. dry_run=True only describes what would happen."""
    action, args = _args(fix, dry_run)
    env = {**os.environ, "KUBE_SENTINEL_ALLOW_ACTIONS": "true"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(action, args)
    if result.is_error:
        raise RuntimeError(" ".join(getattr(c, "text", "") for c in result.content) or "action failed")
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def verify(deployment: str) -> dict[str, Any]:
    """Wait until the deployment is healthy again (all replicas ready, rollout finished)."""
    start = time.monotonic()
    state = "unknown"
    while time.monotonic() - start < VERIFY_TIMEOUT:
        d = await asyncio.to_thread(k8s.describe_deployment, deployment, "default")
        state = d.get("state", "unknown")
        if state == "healthy":
            pods = await asyncio.to_thread(k8s.list_pods, "default", True)
            still_bad = [p["name"] for p in pods if p.get("app") == deployment]
            if not still_bad:
                return {"healthy": True, "state": state, "seconds": round(time.monotonic() - start)}
        await asyncio.sleep(5)
    return {"healthy": False, "state": state, "seconds": round(time.monotonic() - start)}
