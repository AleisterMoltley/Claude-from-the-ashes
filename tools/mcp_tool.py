"""MCPTool — MCP server connection + proxy tools."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from tool_registry import BaseTool, ToolResult, ToolContext

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    transport: str = "stdio"


class MCPConnection:
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._session = None
        self._transport_ctx = None
        self._tools: list[dict] = []
        self._connected = False

    async def connect(self) -> list[dict]:
        try:
            from mcp import ClientSession
            if self.config.transport == "stdio" and self.config.command:
                from mcp.client.stdio import stdio_client, StdioServerParameters
                params = StdioServerParameters(command=self.config.command, args=self.config.args, env=self.config.env or None)
                self._transport_ctx = stdio_client(params)
                read, write = await self._transport_ctx.__aenter__()
            elif self.config.transport == "sse" and self.config.url:
                from mcp.client.sse import sse_client
                self._transport_ctx = sse_client(self.config.url)
                read, write = await self._transport_ctx.__aenter__()
            else:
                raise ValueError(f"Invalid MCP config for {self.config.name}")

            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
            tools_result = await self._session.list_tools()
            self._tools = [{"name": t.name, "description": t.description or "",
                            "input_schema": t.inputSchema if hasattr(t, 'inputSchema') else {}} for t in tools_result.tools]
            self._connected = True
            logger.info(f"MCP {self.config.name}: {len(self._tools)} tools")
            return self._tools
        except ImportError:
            logger.error("mcp package not installed"); return []
        except Exception as e:
            logger.error(f"MCP {self.config.name} failed: {e}"); return []

    async def call_tool(self, tool_name: str, arguments: dict):
        if not self._session: raise RuntimeError("Not connected")
        return await self._session.call_tool(tool_name, arguments)

    async def disconnect(self):
        try:
            if self._session: await self._session.__aexit__(None, None, None)
            if self._transport_ctx: await self._transport_ctx.__aexit__(None, None, None)
        except Exception: pass
        self._connected = False


class MCPProxyTool(BaseTool):
    def __init__(self, server_name: str, tool_info: dict, connection: MCPConnection):
        self.name = f"mcp__{server_name}__{tool_info['name']}"
        self.description = f"[MCP: {server_name}] {tool_info.get('description', tool_info['name'])}"
        self._tool_name = tool_info["name"]
        self._schema = tool_info.get("input_schema", {"type": "object", "properties": {}})
        self._conn = connection
        self.is_read_only = False

    def get_input_schema(self) -> dict: return self._schema
    def needs_confirmation(self, params, config): return True

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        try:
            result = await self._conn.call_tool(self._tool_name, params)
            parts = []
            if hasattr(result, 'content'):
                for b in result.content:
                    if hasattr(b, 'text'): parts.append(b.text)
                    else: parts.append(str(b))
            output = "\n".join(parts) or str(result)
            is_err = getattr(result, 'isError', False)
            return ToolResult(output=output, is_error=is_err, error=output if is_err else "")
        except Exception as e:
            return ToolResult(error=f"MCP call failed: {e}", is_error=True)


class MCPManager:
    def __init__(self):
        self._connections: dict[str, MCPConnection] = {}

    async def connect_server(self, config: MCPServerConfig) -> list[MCPProxyTool]:
        conn = MCPConnection(config)
        tools_info = await conn.connect()
        if not tools_info: return []
        self._connections[config.name] = conn
        return [MCPProxyTool(config.name, ti, conn) for ti in tools_info]

    async def disconnect_all(self):
        for c in self._connections.values(): await c.disconnect()
        self._connections.clear()
