"""MCP (Model Context Protocol) client — bring external tool servers into the registry.

MCP (modelcontextprotocol.io) is the 2026 de-facto standard for agent tool
integration. Hermes has `tools/mcp_tool.py` (2195 LOC) covering stdio/SSE
transports, OAuth, dynamic reload, and hot-swap. Our minimal version:

  - One function: `register_mcp_servers(specs) -> list[str]`
  - Supports **stdio** transport only (launches a subprocess per server).
  - On startup, spawn the server, list its tools, and register each as a
    first-class `Tool` in `REGISTRY` under toolset `"mcp:<server_name>"`.
  - Each registered tool's handler spins up a fresh session per call
    (simple, slow-ish, but avoids async-loop headaches with persistent
    stdio connections inside a sync agent loop).

Skipped (vs hermes, for scope):
  - SSE / HTTP transport (2026 most servers ship stdio first)
  - OAuth
  - Dynamic tool list reload via `notifications/tools/list_changed`
  - Per-server connection pooling
  - Tool-result size persistence

The `mcp` Python SDK is imported lazily so the agent works without it
when MCP isn't configured.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .tools import REGISTRY, Tool


log = logging.getLogger(__name__)


def _require_mcp() -> tuple[Any, Any, Any]:
    """Import the `mcp` SDK lazily. Raises a helpful error if missing."""
    try:
        from mcp import ClientSession, StdioServerParameters  # type: ignore
        from mcp.client.stdio import stdio_client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "MCP support requires the `mcp` package. Install with: pip install mcp"
        ) from e
    return ClientSession, StdioServerParameters, stdio_client


async def _list_tools_async(server_params: Any) -> list[Any]:
    """Connect to an stdio MCP server, list its tools, and disconnect."""
    _, _, stdio_client = _require_mcp()
    from mcp import ClientSession  # type: ignore

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return list(result.tools)


async def _call_tool_async(
    server_params: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Connect, call one tool, disconnect. Returns text content concatenated."""
    _, _, stdio_client = _require_mcp()
    from mcp import ClientSession  # type: ignore

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            # MCP tool results are a list of content items; concatenate text.
            parts: list[str] = []
            for item in getattr(result, "content", []) or []:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
                else:
                    parts.append(json.dumps(
                        getattr(item, "model_dump", lambda: {})(),
                        ensure_ascii=False, default=str,
                    ))
            if getattr(result, "isError", False):
                return f"[mcp-error] {' '.join(parts) or 'unknown error'}"
            return "\n".join(parts) if parts else "(empty)"


def _run_sync(coro: Any) -> Any:
    """Run an async coroutine from sync code, creating a fresh event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(coro)
    # Inside an existing loop (rare for our sync agent, but defensive):
    # run on a dedicated thread with its own loop.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _make_handler(server_params: Any, tool_name: str) -> Any:
    """Build a sync handler that forwards a call to the MCP server."""

    def handler(**kwargs: Any) -> str:
        try:
            return _run_sync(_call_tool_async(server_params, tool_name, kwargs))
        except Exception as e:
            return f"[mcp-error] {type(e).__name__}: {e}"

    # Hide `ctx` — this tool doesn't want it.
    handler.__name__ = tool_name
    return handler


def _schema_from_mcp_tool(mcp_tool: Any) -> dict[str, Any]:
    """Turn an MCP Tool (from `session.list_tools()`) into an OpenAI tool schema."""
    name = getattr(mcp_tool, "name", "") or "unnamed_mcp_tool"
    description = getattr(mcp_tool, "description", "") or f"MCP tool {name}"
    input_schema = getattr(mcp_tool, "inputSchema", None) or {
        "type": "object", "properties": {}, "required": [],
    }
    # Normalize shape: OpenAI expects {type, properties, required}.
    if isinstance(input_schema, dict):
        input_schema = {
            "type": input_schema.get("type", "object"),
            "properties": input_schema.get("properties", {}),
            "required": input_schema.get("required", []),
        }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


def register_mcp_servers(
    servers: dict[str, dict[str, Any]],
    *,
    registry: Any = REGISTRY,
    quiet: bool = False,
) -> list[str]:
    """Connect to each MCP server, enumerate its tools, register them.

    Each entry in `servers`:

        "my_server": {
            "command": "uvx",
            "args": ["mcp-server-time"],
            "env": {"FOO": "bar"},          # optional
        }

    Returns the list of registered tool names. Failures are logged but
    don't break the agent — the rest of the registry stays usable.
    """
    _, StdioServerParameters, _ = _require_mcp()

    registered: list[str] = []
    for server_name, spec in servers.items():
        command = spec.get("command")
        if not command:
            log.warning("MCP server %s: missing 'command'", server_name)
            continue
        args = spec.get("args") or []
        env = spec.get("env")

        params = StdioServerParameters(command=command, args=args, env=env)

        try:
            mcp_tools = _run_sync(_list_tools_async(params))
        except Exception as e:
            if not quiet:
                print(f"[mcp] failed to list tools from {server_name}: {e}")
            continue

        toolset = f"mcp:{server_name}"
        for mcp_tool in mcp_tools:
            schema = _schema_from_mcp_tool(mcp_tool)
            tool_name = schema["function"]["name"]
            t = Tool(
                name=tool_name,
                description=schema["function"]["description"],
                schema=schema,
                func=_make_handler(params, tool_name),
                toolset=toolset,
                emoji="🔌",
                _wants_ctx=False,
            )
            registry.register(t)
            registered.append(tool_name)
            if not quiet:
                print(f"[mcp] registered {tool_name!r} from {server_name}")

    return registered
