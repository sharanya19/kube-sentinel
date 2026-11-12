r"""The investigation graph.

    START -> investigate --(wants a tool)--> tools --> investigate ...
                         \--(done, or out of budget)--> write_report -> END

`investigate` is Gemini (see models.py) with the MCP tools bound; it decides what to look at next.
`tools` runs the calls through MCP. `write_report` turns the whole investigation into a
structured Report (root cause, evidence, one allowlisted fix).
"""

from __future__ import annotations

import os
import time
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .models import ModelPool
from .prompts import INVESTIGATE_PROMPT, REPORT_PROMPT, OUT_OF_BUDGET
from .report import Report

MAX_TOOL_CALLS = int(os.environ.get("KUBE_SENTINEL_MAX_TOOL_CALLS", "20"))


class State(TypedDict, total=False):
    alert: str
    messages: Annotated[list[AnyMessage], add_messages]
    tool_calls: int
    trace: list[dict]
    report: Report


def build_graph(tools: list, pool: ModelPool, on_tool: Callable[[str, dict], None] | None = None):
    tool_node = ToolNode(tools, handle_tool_errors=True)

    async def investigate(state: State) -> dict:
        msgs = [SystemMessage(INVESTIGATE_PROMPT), *state["messages"]]
        if state.get("tool_calls", 0) >= MAX_TOOL_CALLS:
            msgs.append(HumanMessage(OUT_OF_BUDGET))
            return {"messages": [await pool.ainvoke(msgs)]}
        return {"messages": [await pool.ainvoke(msgs, tools=tools)]}

    async def run_tools(state: State) -> dict:
        calls = state["messages"][-1].tool_calls
        trace = list(state.get("trace", []))
        for c in calls:
            trace.append({"tool": c["name"], "args": c["args"]})
            if on_tool:
                on_tool(c["name"], c["args"])
        out = await tool_node.ainvoke({"messages": state["messages"]})
        return {"messages": out["messages"], "tool_calls": state.get("tool_calls", 0) + len(calls),
                "trace": trace}

    async def write_report(state: State) -> dict:
        msgs = [SystemMessage(REPORT_PROMPT), *_flatten(state["messages"]),
                HumanMessage("Write the incident report now.")]
        return {"report": await pool.ainvoke(msgs, schema=Report)}

    def next_step(state: State) -> str:
        last = state["messages"][-1]
        wants_tool = isinstance(last, AIMessage) and bool(last.tool_calls)
        return "tools" if wants_tool and state.get("tool_calls", 0) < MAX_TOOL_CALLS else "write_report"

    g = StateGraph(State)
    g.add_node("investigate", investigate)
    g.add_node("tools", run_tools)
    g.add_node("write_report", write_report)
    g.add_edge(START, "investigate")
    g.add_conditional_edges("investigate", next_step, ["tools", "write_report"])
    g.add_edge("tools", "investigate")
    g.add_edge("write_report", END)
    return g.compile()


def _flatten(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Replay the investigation as plain text for the report writer.

    Structured-output calls don't need tool-call bookkeeping, and some models reject a
    conversation that ends with tool calls they can't make, so tool traffic becomes text.
    """
    out: list[AnyMessage] = []
    for m in messages:
        if isinstance(m, AIMessage):
            text = _text(m.content)
            calls = "; ".join(f"{c['name']}({c['args']})" for c in m.tool_calls)
            body = "\n".join(x for x in (text, f"[called: {calls}]" if calls else "") if x)
            if body:
                out.append(AIMessage(body))
        elif m.type == "tool":
            out.append(HumanMessage(f"[result of {m.name}]\n{_text(m.content)}"))
        else:
            out.append(m)
    return out


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


async def investigate(alert: str, tools: list, on_tool: Callable[[str, dict], None] | None = None,
                      pool: ModelPool | None = None) -> dict:
    """Run one investigation. Returns report, tool trace, tool-call count and seconds taken."""
    pool = pool or ModelPool()
    graph = build_graph(tools, pool, on_tool)
    start = time.monotonic()
    final = await graph.ainvoke({"alert": alert, "messages": [HumanMessage(alert)], "tool_calls": 0,
                                 "trace": []},
                                {"recursion_limit": 2 * MAX_TOOL_CALLS + 10})
    return {
        "report": final["report"],
        "trace": final.get("trace", []),
        "tool_calls": final.get("tool_calls", 0),
        "model_requests": pool.requests,  # free-tier quotas count these
        "models": pool.used,
        "seconds": round(time.monotonic() - start, 1),
    }
