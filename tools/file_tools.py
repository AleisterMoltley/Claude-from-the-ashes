"""FileWriteTool + FileEditTool — Create/overwrite and partial edit."""
from pathlib import Path
from tool_registry import BaseTool, ToolResult, ToolContext


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "Create or overwrite a file. Creates parent directories automatically."
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Complete file content."},
            },
            "required": ["path", "content"],
        }

    def needs_confirmation(self, params, config):
        return not (config and config.auto_approve_write)

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = Path(params.get("path", ""))
        if not path.is_absolute(): path = Path(context.working_dir) / path
        path = path.resolve()
        content = params.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(output=f"Written {len(content)} bytes ({content.count(chr(10))+1} lines) to {path}")
        except Exception as e:
            return ToolResult(error=f"Failed to write {path}: {e}", is_error=True)


class FileEditTool(BaseTool):
    name = "file_edit"
    description = "Edit a file by replacing an exact unique string with another. old_str must appear exactly once."
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "old_str": {"type": "string", "description": "Exact string to find (must be unique)."},
                "new_str": {"type": "string", "description": "Replacement string."},
            },
            "required": ["path", "old_str"],
        }

    def needs_confirmation(self, params, config):
        return not (config and config.auto_approve_write)

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = Path(params.get("path", ""))
        if not path.is_absolute(): path = Path(context.working_dir) / path
        path = path.resolve()
        if not path.exists(): return ToolResult(error=f"Not found: {path}", is_error=True)
        old_str = params.get("old_str", "")
        new_str = params.get("new_str", "")
        try: content = path.read_text(encoding="utf-8")
        except Exception as e: return ToolResult(error=f"Read failed: {e}", is_error=True)
        count = content.count(old_str)
        if count == 0: return ToolResult(error="old_str not found in file.", is_error=True)
        if count > 1: return ToolResult(error=f"old_str found {count} times (must be unique).", is_error=True)
        path.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")
        return ToolResult(output=f"Edited {path}: {len(old_str)}→{len(new_str)} chars.")
