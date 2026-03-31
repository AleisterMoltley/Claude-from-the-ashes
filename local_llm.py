"""
Local LLM Client — OpenAI-compatible API for local models.
Supports Ollama, LM Studio, vLLM, llama.cpp server, etc.

Maps the Anthropic-style tool-use protocol to OpenAI's function-calling format,
so the rest of Compagnon doesn't need to change.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LocalMessage:
    """Minimal response object matching what QueryEngine expects."""
    content: list  # list of content blocks
    stop_reason: str = "end_turn"
    usage: Any = None


@dataclass
class LocalUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class LocalTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class LocalToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = None

    def __post_init__(self):
        if self.input is None:
            self.input = {}


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool schemas to OpenAI function-calling format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


def _anthropic_messages_to_openai(messages: list[dict], system: str = "") -> list[dict]:
    """Convert Anthropic message format to OpenAI format."""
    result = []

    if system:
        result.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if isinstance(content, list):
            # Handle mixed content blocks
            text_parts = []
            tool_calls = []
            tool_results = []

            for block in content:
                if not isinstance(block, dict):
                    continue

                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })

                elif block.get("type") == "tool_result":
                    # Tool results become assistant function responses
                    tool_content = block.get("content", "")
                    if isinstance(tool_content, list):
                        tool_content = " ".join(
                            b.get("text", "") for b in tool_content if isinstance(b, dict)
                        )
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(tool_content),
                    })

            if role == "assistant" and tool_calls:
                msg_out = {"role": "assistant", "content": " ".join(text_parts) if text_parts else None, "tool_calls": tool_calls}
                result.append(msg_out)
            elif tool_results:
                result.extend(tool_results)
            elif text_parts:
                result.append({"role": role, "content": " ".join(text_parts)})
            else:
                result.append({"role": role, "content": str(content)})

    return result


def _openai_response_to_anthropic(data: dict) -> LocalMessage:
    """Convert OpenAI response to Anthropic-like message."""
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    finish = choice.get("finish_reason", "stop")

    content_blocks = []

    # Text content
    text = msg.get("content")
    if text:
        content_blocks.append(LocalTextBlock(text=text))

    # Tool calls
    tool_calls = msg.get("tool_calls", [])
    for tc in tool_calls:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {"raw": fn.get("arguments", "")}

        content_blocks.append(LocalToolUseBlock(
            id=tc.get("id", f"call_{hash(fn.get('name',''))}"),
            name=fn.get("name", ""),
            input=args,
        ))

    # Usage
    usage_data = data.get("usage", {})
    usage = LocalUsage(
        input_tokens=usage_data.get("prompt_tokens", 0),
        output_tokens=usage_data.get("completion_tokens", 0),
    )

    stop_reason = "end_turn" if finish == "stop" else "tool_use" if tool_calls else "end_turn"

    return LocalMessage(content=content_blocks, stop_reason=stop_reason, usage=usage)


class LocalLLMClient:
    """
    Client for OpenAI-compatible local LLM servers.
    Drop-in replacement for the Anthropic client in QueryEngine.
    """

    def __init__(self, base_url: str, api_key: str = "local", model: str = "qwen2.5:32b"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=300.0,
        )

    def create(
        self,
        model: str = "",
        max_tokens: int = 4096,
        system: str = "",
        messages: list[dict] = None,
        tools: list[dict] = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> LocalMessage:
        """Non-streaming completion (used by auto-compact)."""
        openai_messages = _anthropic_messages_to_openai(messages or [], system)
        openai_tools = _anthropic_tools_to_openai(tools) if tools else None

        body = {
            "model": model or self.model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if openai_tools:
            body["tools"] = openai_tools
            body["tool_choice"] = "auto"

        resp = self._http.post("/chat/completions", json=body)
        resp.raise_for_status()
        return _openai_response_to_anthropic(resp.json())

    def stream(
        self,
        model: str = "",
        max_tokens: int = 4096,
        system: str = "",
        messages: list[dict] = None,
        tools: list[dict] = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> "LocalStreamContext":
        """Streaming completion — returns context manager matching Anthropic's stream API."""
        openai_messages = _anthropic_messages_to_openai(messages or [], system)
        openai_tools = _anthropic_tools_to_openai(tools) if tools else None

        body = {
            "model": model or self.model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if openai_tools:
            body["tools"] = openai_tools
            body["tool_choice"] = "auto"

        return LocalStreamContext(self._http, body)


class LocalStreamContext:
    """Context manager that mimics Anthropic's stream interface."""

    def __init__(self, http: httpx.Client, body: dict):
        self._http = http
        self._body = body
        self._response = None
        self._final_message: Optional[LocalMessage] = None

    def __enter__(self):
        # Do the streaming request and collect chunks
        # Many local LLM servers have quirky SSE streaming, so we collect
        # the full response for reliability, then replay as events
        self._body["stream"] = False  # Safer for local servers
        resp = self._http.post("/chat/completions", json=self._body)
        resp.raise_for_status()
        self._final_message = _openai_response_to_anthropic(resp.json())
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        """Yield synthetic events matching Anthropic's stream format."""
        if not self._final_message:
            return

        for i, block in enumerate(self._final_message.content):
            # content_block_start
            yield _SyntheticEvent("content_block_start", content_block=block, index=i)

            # content_block_delta for text
            if block.type == "text" and block.text:
                yield _SyntheticEvent("content_block_delta", delta=_TextDelta(block.text))

            # content_block_stop
            yield _SyntheticEvent("content_block_stop", index=i)

        yield _SyntheticEvent("message_delta")

    def get_final_message(self) -> LocalMessage:
        return self._final_message


@dataclass
class _TextDelta:
    text: str = ""
    type: str = "text_delta"


@dataclass
class _SyntheticEvent:
    type: str
    content_block: Any = None
    delta: Any = None
    index: int = 0
