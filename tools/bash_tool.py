"""
BashTool — Shell command execution with safety checks.
"""
import asyncio
import os
import shlex
from tool_registry import BaseTool, ToolResult, ToolContext
from config import SAFE_BASH_COMMANDS

MAX_OUTPUT_LENGTH = 100_000
DEFAULT_TIMEOUT = 120


class BashTool(BaseTool):
    name = "bash"
    description = (
        "Execute a bash command. Use for running scripts, installing packages, "
        "searching files, git operations, and system commands. "
        "Long-running commands are terminated after the timeout."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to execute."},
                "timeout": {"type": "integer", "description": f"Timeout in seconds (default: {DEFAULT_TIMEOUT})."},
                "working_dir": {"type": "string", "description": "Working directory override."},
            },
            "required": ["command"],
        }

    def _extract_base_command(self, command: str) -> str:
        cmd = command.strip()
        for prefix in ["sudo ", "env ", "nohup "]:
            if cmd.startswith(prefix):
                cmd = cmd[len(prefix):]
        try:
            tokens = shlex.split(cmd)
            if tokens:
                return os.path.basename(tokens[0])
        except ValueError:
            pass
        parts = cmd.split()
        return os.path.basename(parts[0]) if parts else ""

    def needs_confirmation(self, params: dict, config) -> bool:
        command = params.get("command", "")
        base_cmd = self._extract_base_command(command)
        if config and base_cmd in config.require_confirmation_for:
            return True
        if config and config.auto_approve_bash_safe and base_cmd in SAFE_BASH_COMMANDS:
            return False
        if config and config.auto_approve_bash_destructive:
            return False
        return True

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        command = params.get("command", "")
        timeout = params.get("timeout", DEFAULT_TIMEOUT)
        cwd = params.get("working_dir", context.working_dir)
        if not command.strip():
            return ToolResult(error="Empty command", is_error=True)
        try:
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd, env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                return ToolResult(error=f"Command timed out after {timeout}s", is_error=True)

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            if len(stdout) > MAX_OUTPUT_LENGTH:
                half = MAX_OUTPUT_LENGTH // 2
                stdout = stdout[:half] + f"\n\n... [{len(stdout) - MAX_OUTPUT_LENGTH} chars truncated] ...\n\n" + stdout[-half:]

            parts = []
            if stdout.strip(): parts.append(stdout.strip())
            if stderr.strip(): parts.append(f"STDERR:\n{stderr.strip()}")
            if proc.returncode != 0: parts.append(f"Exit code: {proc.returncode}")
            output = "\n".join(parts) if parts else "(no output)"
            return ToolResult(output=output, is_error=proc.returncode != 0,
                              error=stderr.strip() if proc.returncode != 0 else "",
                              metadata={"exit_code": proc.returncode})
        except Exception as e:
            return ToolResult(error=f"Failed to execute: {e}", is_error=True)
