"""
Tool Registry System — mirrors Claude Code's Tool.ts and tools.ts.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from config import CompagnonConfig


@dataclass
class ToolResult:
    output: str = ""
    error: str = ""
    is_error: bool = False
    metadata: dict = field(default_factory=dict)
    def to_content(self) -> list[dict]:
        if self.is_error:
            return [{"type": "text", "text": f"Error: {self.error}"}]
        return [{"type": "text", "text": self.output}]


@dataclass
class ToolContext:
    working_dir: str = "."
    config: Optional[CompagnonConfig] = None
    session_id: str = ""
    agent_id: str = "main"
    depth: int = 0
    permission_callback: Any = None


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    is_read_only: bool = False
    is_enabled_default: bool = True

    @abstractmethod
    def get_input_schema(self) -> dict: ...

    @abstractmethod
    async def execute(self, params: dict, context: ToolContext) -> ToolResult: ...

    def needs_confirmation(self, params: dict, config: CompagnonConfig) -> bool:
        if self.is_read_only:
            return False
        return True

    def is_enabled(self) -> bool:
        return self.is_enabled_default

    def to_api_schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.get_input_schema()}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._mcp_tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool): self._tools[tool.name] = tool
    def register_mcp(self, tool: BaseTool): self._mcp_tools[tool.name] = tool
    def unregister(self, name: str):
        self._tools.pop(name, None); self._mcp_tools.pop(name, None)

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name) or self._mcp_tools.get(name)

    def get_all_enabled(self) -> list[BaseTool]:
        seen, result = set(), []
        for tool in sorted(self._tools.values(), key=lambda t: t.name):
            if tool.is_enabled() and tool.name not in seen:
                result.append(tool); seen.add(tool.name)
        for tool in sorted(self._mcp_tools.values(), key=lambda t: t.name):
            if tool.is_enabled() and tool.name not in seen:
                result.append(tool); seen.add(tool.name)
        return result

    def get_api_schemas(self) -> list[dict]:
        return [t.to_api_schema() for t in self.get_all_enabled()]

    def list_names(self) -> list[str]:
        return [t.name for t in self.get_all_enabled()]
