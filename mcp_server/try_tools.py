"""Talk to the MCP server the same way the agent will, and print what each tool returns.

    python -m mcp_server.try_tools                       # list tools + run cluster_overview
    python -m mcp_server.try_tools get_events '{"object_name": "redis-cart"}'
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    tool = sys.argv[1] if len(sys.argv) > 1 else "cluster_overview"
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"Server offers {len(tools)} tools:")
            for t in tools:
                ro = "read-only" if t.annotations and t.annotations.read_only_hint else "CHANGES CLUSTER"
                print(f"  - {t.name:<22} [{ro}]")
            print(f"\n>>> {tool}({json.dumps(args)})\n")
            result = await session.call_tool(tool, args)
            if result.is_error:
                print("ERROR:", result.content[0].text if result.content else result)
            elif result.structured_content is not None:
                data = result.structured_content
                # list-returning tools are wrapped as {"result": [...]}
                if set(data) == {"result"}:
                    data = data["result"]
                print(json.dumps(data, indent=2))
            else:
                print("\n".join(c.text for c in result.content if hasattr(c, "text")))


if __name__ == "__main__":
    asyncio.run(main())
