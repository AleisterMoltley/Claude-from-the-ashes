"""
QueryEngine v2 — Core agentic loop with streaming, auto-compact, cost tracking.
Inspired by Claude Code's QueryEngine.ts, query.ts, and autoCompact.ts.
"""
from __future__ import annotations
import json
import logging
import time
from typing import Any, AsyncGenerator, Callable, Optional
from dataclasses import dataclass, field

import anthropic

from tool_registry import ToolRegistry, ToolContext, ToolResult
from config import CompagnonConfig
from token_tracker import TokenUsage, CostTracker
from auto_compact import should_auto_compact, compact_conversation, estimate_token_count
from local_llm import LocalLLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Compagnon — an autonomous AI agent with direct computer access.
You execute commands, read/write files, search the web, manage memory, and spawn sub-agents.

## Principles
- Be direct and efficient. Execute commands for real info rather than guessing.
- Debug errors systematically. Read the actual error, check the file, fix it.
- Use memory to persist important information across sessions.
- Break complex tasks into sub-tasks via the agent tool.
- Verify your work — run tests, check outputs, validate results.

## Working Directory: {working_dir}
## Tools: {tool_names}

## Memory
{memory_context}

## Custom Instructions
{custom_instructions}

## Guidelines
- Prefer file_read/file_write/file_edit over bash cat/echo for file ops
- Use web_search for current information you don't have
- Use memory_write to save important discoveries
- Use agent for isolated subtasks needing focused attention
- Explain before executing destructive operations
"""

SUB_AGENT_PROMPT = """You are a sub-agent of Compagnon working on a specific subtask.
Complete thoroughly and report findings.

Working directory: {working_dir}
Tools: {tool_names}

Task: {task}
"""


@dataclass
class StreamEvent:
    """Event emitted during streaming."""
    type: str  # "text", "tool_call", "tool_result", "thinking", "compact", "error", "done"
    text: str = ""
    tool_name: str = ""
    tool_params: dict = field(default_factory=dict)
    tool_result: Optional[ToolResult] = None
    usage: Optional[TokenUsage] = None


@dataclass
class QueryResult:
    text: str = ""
    tool_calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    error: str = ""
    messages: list[dict] = field(default_factory=list)
    was_compacted: bool = False


class QueryEngine:
    def __init__(
        self,
        config: CompagnonConfig,
        registry: ToolRegistry,
        memory_context: str = "",
        cost_tracker: Optional[CostTracker] = None,
    ):
        self.config = config
        self.registry = registry
        self.memory_context = memory_context
        self.cost_tracker = cost_tracker

        # Dual-mode: Anthropic API or local LLM
        if config.is_local:
            self._local_client = LocalLLMClient(
                base_url=config.local_base_url,
                api_key=config.local_api_key,
                model=config.local_model,
            )
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            self._local_client = None

    def _build_system(self, working_dir: str, task: str = "") -> str:
        names = ", ".join(self.registry.list_names())
        if task:
            return SUB_AGENT_PROMPT.format(working_dir=working_dir, tool_names=names, task=task)
        return SYSTEM_PROMPT.format(
            working_dir=working_dir, tool_names=names,
            memory_context=self.memory_context or "(none)",
            custom_instructions=self.config.custom_instructions or "(none)",
        )

    async def query_streaming(
        self,
        messages: list[dict],
        context: ToolContext,
        system_prompt: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Streaming agentic loop. Yields StreamEvents as they happen.
        This is the primary interface — Telegram UI consumes these events.
        """
        if not system_prompt:
            system_prompt = self._build_system(context.working_dir)

        tools = self.registry.get_api_schemas()
        consecutive_errors = 0
        total_usage = TokenUsage()
        total_tool_calls = 0

        for turn in range(self.config.max_tool_calls_per_turn):
            # ── Auto-compact check ──
            if should_auto_compact(messages, self.config.model):
                yield StreamEvent(type="compact", text="Compacting context...")
                messages, summary = await compact_conversation(messages, self.config)
                if summary:
                    yield StreamEvent(type="compact", text=f"Compacted ({estimate_token_count(messages)} tokens)")

            # ── Budget check ──
            if self.cost_tracker and self.cost_tracker.today.is_over_budget(self.config.daily_budget_usd):
                yield StreamEvent(type="error", text=f"Daily budget exceeded (${self.config.daily_budget_usd})")
                return

            # ── Stream API call ──
            try:
                text_parts = []
                tool_uses = []
                turn_usage = TokenUsage()

                active_model = self.config.active_model

                # Build stream context for either backend
                if self._local_client:
                    stream_ctx = self._local_client.stream(
                        model=active_model,
                        max_tokens=self.config.max_tokens,
                        system=system_prompt,
                        messages=messages,
                        tools=tools if tools else None,
                        temperature=self.config.temperature,
                    )
                else:
                    stream_ctx = self.client.messages.stream(
                        model=active_model,
                        max_tokens=self.config.max_tokens,
                        system=system_prompt,
                        messages=messages,
                        tools=tools if tools else anthropic.NOT_GIVEN,
                        temperature=self.config.temperature,
                    )

                with stream_ctx as stream:
                    current_text = ""
                    for event in stream:
                        if event.type == "content_block_start":
                            if hasattr(event.content_block, 'text'):
                                current_text = ""

                        elif event.type == "content_block_delta":
                            if hasattr(event.delta, 'text'):
                                current_text += event.delta.text
                                yield StreamEvent(type="text", text=event.delta.text)

                        elif event.type in ("content_block_stop", "message_delta"):
                            pass

                    response = stream.get_final_message()

                consecutive_errors = 0

                # Extract usage
                if response.usage:
                    turn_usage.add(response.usage)
                    total_usage.add(response.usage)

                # Record cost
                if self.cost_tracker and not self.config.is_local:
                    self.cost_tracker.record(turn_usage, active_model)

                # Process content blocks
                assistant_content = []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        tool_uses.append(block)
                        assistant_content.append({
                            "type": "tool_use", "id": block.id,
                            "name": block.name, "input": block.input,
                        })

                messages.append({"role": "assistant", "content": assistant_content})

                # No tool use → done
                if not tool_uses:
                    yield StreamEvent(type="done", text="\n".join(text_parts), usage=total_usage)
                    return

                # Execute tools
                tool_results = []
                for tu in tool_uses:
                    total_tool_calls += 1
                    yield StreamEvent(type="tool_call", tool_name=tu.name, tool_params=tu.input)

                    tr = await self._execute_tool(tu.name, tu.input, context)
                    yield StreamEvent(type="tool_result", tool_name=tu.name, tool_result=tr)

                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id,
                        "content": tr.to_content(), "is_error": tr.is_error,
                    })

                messages.append({"role": "user", "content": tool_results})

                if response.stop_reason == "end_turn":
                    yield StreamEvent(type="done", text="\n".join(text_parts), usage=total_usage)
                    return

            except anthropic.APIError as e:
                consecutive_errors += 1
                yield StreamEvent(type="error", text=f"API error: {e}")
                if consecutive_errors >= self.config.max_consecutive_errors:
                    return

        yield StreamEvent(type="done", text="(max tool calls reached)", usage=total_usage)

    async def query(self, messages: list[dict], context: ToolContext, system_prompt: str = "") -> QueryResult:
        """Non-streaming wrapper for compatibility."""
        result = QueryResult(messages=list(messages))
        async for event in self.query_streaming(messages, context, system_prompt):
            if event.type == "text":
                result.text += event.text
            elif event.type == "done":
                result.text = event.text or result.text
                if event.usage:
                    result.usage = event.usage
            elif event.type == "error":
                result.error = event.text
            elif event.type == "tool_call":
                result.tool_calls += 1
        result.messages = messages
        return result

    async def _execute_tool(self, name: str, params: dict, context: ToolContext) -> ToolResult:
        tool = self.registry.get(name)
        if not tool:
            return ToolResult(error=f"Unknown tool: {name}", is_error=True)
        if tool.needs_confirmation(params, context.config):
            if context.permission_callback:
                approved = await context.permission_callback(name, params)
                if not approved:
                    return ToolResult(error=f"Denied by user: {name}", is_error=True)
        try:
            return await tool.execute(params, context)
        except Exception as e:
            logger.error(f"Tool {name} error: {e}", exc_info=True)
            return ToolResult(error=f"Tool failed: {e}", is_error=True)

    async def run_agent(self, task: str, context: ToolContext) -> str:
        system = self._build_system(context.working_dir, task=task)
        messages = [{"role": "user", "content": task}]
        result = await self.query(messages, context, system)
        return result.text or result.error or "(no response)"

    async def chat(self, user_message: str, messages: list[dict], context: ToolContext) -> QueryResult:
        messages.append({"role": "user", "content": user_message})
        return await self.query(messages, context)
