"""AgentTool — Spawn sub-agents for isolated subtasks."""
from __future__ import annotations
import uuid
from typing import TYPE_CHECKING
from tool_registry import BaseTool, ToolResult, ToolContext
if TYPE_CHECKING:
    from query_engine import QueryEngine


class AgentTool(BaseTool):
    name = "agent"
    description = (
        "Spawn a sub-agent for a complex subtask. The sub-agent gets its own context "
        "and can use all tools. Use for parallelizable work or focused research."
    )
    is_read_only = False

    def __init__(self, query_engine_factory=None):
        self._factory = query_engine_factory

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Self-contained task description with all needed context."},
                "working_dir": {"type": "string", "description": "Working directory override."},
            },
            "required": ["task"],
        }

    def needs_confirmation(self, params, config): return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task = params.get("task", "")
        if not task.strip(): return ToolResult(error="Empty task", is_error=True)
        max_depth = context.config.max_agent_depth if context.config else 3
        if context.depth >= max_depth:
            return ToolResult(error=f"Max agent depth ({max_depth}) reached.", is_error=True)
        if not self._factory:
            return ToolResult(error="Agent spawning not configured.", is_error=True)
        aid = f"agent-{uuid.uuid4().hex[:8]}"
        try:
            engine = self._factory()
            sub_ctx = ToolContext(
                working_dir=params.get("working_dir", context.working_dir),
                config=context.config, session_id=context.session_id,
                agent_id=aid, depth=context.depth + 1,
                permission_callback=context.permission_callback,
            )
            result = await engine.run_agent(task=task, context=sub_ctx)
            return ToolResult(output=f"[Sub-agent {aid}]\n\n{result}", metadata={"agent_id": aid})
        except Exception as e:
            return ToolResult(error=f"Sub-agent failed: {e}", is_error=True)
