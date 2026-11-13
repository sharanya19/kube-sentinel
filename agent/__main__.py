"""Run one investigation from the command line.

    python -m agent                                   # generic "something is wrong" alert
    python -m agent "Checkout is failing for customers"
"""

from __future__ import annotations

import asyncio
import json
import sys

from .run import run_investigation

DEFAULT_ALERT = "Users report that the Online Boutique shop is not working properly. Investigate."


def main() -> None:
    alert = " ".join(sys.argv[1:]) or DEFAULT_ALERT
    print(f"Alert: {alert}\n")

    def on_tool(name: str, args: dict) -> None:
        print(f"  -> {name}({json.dumps(args) if args else ''})", flush=True)

    def on_switch(failed: str, nxt: str | None, why: str) -> None:
        print(f"  !! {failed}: {why}" + (f"; switching to {nxt}" if nxt else ""), flush=True)

    try:
        result = asyncio.run(run_investigation(alert, on_tool=on_tool, on_switch=on_switch))
    except BaseException as e:  # noqa: BLE001 - show the real reason, not an ExceptionGroup
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        from .models import explain_error
        raise SystemExit(f"Investigation failed: {explain_error(e)}") from None
    print(f"\n{'=' * 70}\n{result['report'].to_text()}\n{'=' * 70}")
    print(f"{result['tool_calls']} tool calls in {result['model_requests']} model requests, {result['seconds']}s, model {result['model']}")
    if result["status"] == "no_incident":
        print(f"Saved as {result['id']}: no active incident, nothing to approve.")
    else:
        print(f"Saved incident {result['id']} (open the approval UI to approve or reject the fix)")


if __name__ == "__main__":
    main()
