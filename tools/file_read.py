"""FileReadTool — Read file contents with line ranges."""
import mimetypes
import base64
from pathlib import Path
from tool_registry import BaseTool, ToolResult, ToolContext

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TEXT_LENGTH = 200_000


class FileReadTool(BaseTool):
    name = "file_read"
    description = "Read file contents. Supports text files and images (base64). Can read specific line ranges."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "start_line": {"type": "integer", "description": "Start line (1-indexed)."},
                "end_line": {"type": "integer", "description": "End line (1-indexed, inclusive)."},
            },
            "required": ["path"],
        }

    def needs_confirmation(self, params, config): return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = Path(params.get("path", ""))
        if not path.is_absolute():
            path = Path(context.working_dir) / path
        path = path.resolve()
        if not path.exists(): return ToolResult(error=f"File not found: {path}", is_error=True)
        if not path.is_file(): return ToolResult(error=f"Not a file: {path}", is_error=True)
        if path.stat().st_size > MAX_FILE_SIZE:
            return ToolResult(error=f"File too large: {path.stat().st_size / 1024 / 1024:.1f}MB", is_error=True)

        mime, _ = mimetypes.guess_type(str(path))
        if mime and mime.startswith("image/"):
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode()
            return ToolResult(output=f"[Image: {path.name}, {mime}, {len(data)} bytes]",
                              metadata={"type": "image", "mime": mime, "base64": b64})
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try: text = path.read_text(encoding="latin-1")
            except Exception: return ToolResult(error=f"Cannot read: {path}", is_error=True)

        lines = text.split("\n")
        total = len(lines)
        s = params.get("start_line"); e = params.get("end_line")
        if s is not None or e is not None:
            si = max(1, s or 1) - 1
            ei = min(total, e or total)
            numbered = [f"{i:6d}\t{line}" for i, line in enumerate(lines[si:ei], start=si+1)]
            return ToolResult(output=f"[Lines {si+1}-{ei} of {total} in {path}]\n" + "\n".join(numbered))

        if len(text) > MAX_TEXT_LENGTH:
            half = MAX_TEXT_LENGTH // 2
            text = text[:half] + f"\n\n... [{len(text)-MAX_TEXT_LENGTH} chars truncated] ...\n\n" + text[-half:]
        return ToolResult(output=f"[{path} ({total} lines)]\n{text}")
