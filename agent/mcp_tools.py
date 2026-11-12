"""Connect to the Kube-Sentinel MCP server and expose its tools as LangChain tools.

The agent never talks to Kubernetes directly: every look at the cluster goes through MCP.
Only read-only tools are handed to the LLM; fix actions are left to the approval step.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langchain_core.tools import StructuredTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MAX_TOOL_OUTPUT = 6000  # characters; keeps one noisy tool from flooding the context


def _result_text(result: Any) -> str:
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        return f"TOOL ERROR: {text}"
    if result.structured_content is not None:
        data = result.structured_content
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]  # list-returning tools are wrapped as {"result": [...]}
        text = json.dumps(data, default=str)
    else:
        text = "\n".join(getattr(c, "text", "") for c in result.content)
    if len(text) > MAX_TOOL_OUTPUT:
        text = text[:MAX_TOOL_OUTPUT] + f"... [truncated, {len(text)} chars total; narrow the query]"
    return text


def _to_langchain(session: ClientSession, tool: Any) -> StructuredTool:
    async def call(**kwargs: Any) -> str:
        args = {k: v for k, v in kwargs.items() if v is not None}
        return _result_text(await session.call_tool(tool.name, args))

    return StructuredTool(
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=tool.input_schema,
        coroutine=call,
    )


@asynccontextmanager
async def mcp_tools(read_only: bool = True) -> AsyncIterator[list[StructuredTool]]:
    """Start the MCP server as a subprocess and yield its tools for the length of the block."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            if read_only:
                tools = [t for t in tools if t.annotations and t.annotations.read_only_hint]
            yield [_to_langchain(session, t) for t in tools]
