"""
Compagnon Configuration — v2
Token budget tracking, model pricing, MCP presets from Claude Code internals.
"""
import os
import json
from dataclasses import dataclass, field
from pathlib import Path

# ── Model Pricing (from Claude Code's modelCost.ts) ────────────
MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.3},
    "claude-sonnet-4-6": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.3},
    "claude-opus-4-20250514": {"input": 15, "output": 75, "cache_write": 18.75, "cache_read": 1.5},
    "claude-opus-4-6": {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.5},
    "claude-haiku-4-5-20251001": {"input": 1, "output": 5, "cache_write": 1.25, "cache_read": 0.1},
    "claude-3-5-sonnet-20241022": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.3},
    "claude-3-5-haiku-20241022": {"input": 0.8, "output": 4, "cache_write": 1, "cache_read": 0.08},
}

CONTEXT_WINDOWS = {
    "claude-sonnet-4-20250514": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
}

# From autoCompact.ts
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

SAFE_BASH_COMMANDS = {
    "ls", "cat", "head", "tail", "less", "more",
    "find", "grep", "rg", "ag", "ack",
    "wc", "stat", "file", "strings",
    "echo", "printf", "date", "whoami", "pwd", "env",
    "which", "whereis", "type", "man", "help",
    "tree", "du", "df", "free", "top", "ps", "uptime",
    "git", "python", "python3", "pip", "npm", "node", "bun",
    "curl", "wget", "jq", "awk", "sed", "sort", "uniq", "tr", "cut",
    "cd", "pushd", "popd", "basename", "dirname", "realpath",
    "diff", "md5sum", "sha256sum", "cargo", "go", "make",
}


def get_context_window(model: str) -> int:
    for key, val in CONTEXT_WINDOWS.items():
        if key in model or model in key:
            return val
    return 200_000


def get_autocompact_threshold(model: str) -> int:
    window = get_context_window(model)
    effective = window - MAX_OUTPUT_TOKENS_FOR_SUMMARY
    return effective - AUTOCOMPACT_BUFFER_TOKENS


def calculate_cost_usd(model: str, input_tokens: int, output_tokens: int,
                       cache_read: int = 0, cache_write: int = 0) -> float:
    pricing = None
    for key, val in MODEL_PRICING.items():
        if key in model or model in key:
            pricing = val
            break
    if not pricing:
        pricing = MODEL_PRICING["claude-sonnet-4-20250514"]
    return (
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
        + (cache_read / 1_000_000) * pricing["cache_read"]
        + (cache_write / 1_000_000) * pricing["cache_write"]
    )


@dataclass
class CompagnonConfig:
    # ── Provider: "anthropic" or "local" ──
    provider: str = "anthropic"  # "anthropic" | "local"
    anthropic_api_key: str = ""
    # Local LLM (Ollama, LM Studio, vLLM, llama.cpp, etc.)
    local_base_url: str = "http://localhost:11434/v1"  # Ollama default
    local_api_key: str = "ollama"  # Ollama doesn't need a real key
    local_model: str = "qwen2.5:32b"  # Good tool-use model

    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.0
    telegram_token: str = ""
    telegram_allowed_users: list[int] = field(default_factory=list)
    max_tool_calls_per_turn: int = 50
    max_agent_depth: int = 3
    max_consecutive_errors: int = 3
    timeout_seconds: int = 300
    working_dir: str = str(Path.home())
    data_dir: str = str(Path.home() / ".compagnon")
    memory_dir: str = ""
    session_dir: str = ""
    auto_approve_read: bool = True
    auto_approve_bash_safe: bool = True
    auto_approve_write: bool = False
    auto_approve_bash_destructive: bool = False
    require_confirmation_for: list[str] = field(default_factory=lambda: [
        "rm", "sudo", "chmod", "chown", "mkfs", "dd",
        "kill", "pkill", "reboot", "shutdown",
    ])
    mcp_servers: dict = field(default_factory=dict)
    custom_instructions: str = ""
    daily_budget_usd: float = 10.0
    warn_at_usd: float = 8.0
    stream_update_interval: float = 1.5

    @property
    def is_local(self) -> bool:
        return self.provider == "local"

    @property
    def active_model(self) -> str:
        return self.local_model if self.is_local else self.model

    def __post_init__(self):
        if not self.memory_dir:
            self.memory_dir = str(Path(self.data_dir) / "memory")
        if not self.session_dir:
            self.session_dir = str(Path(self.data_dir) / "sessions")

    @classmethod
    def from_env(cls) -> "CompagnonConfig":
        config = cls()
        config.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        config.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        if allowed:
            config.telegram_allowed_users = [int(x.strip()) for x in allowed.split(",") if x.strip()]
        config.model = os.getenv("COMPAGNON_MODEL", config.model)
        config.working_dir = os.getenv("COMPAGNON_WORK_DIR", config.working_dir)
        config.data_dir = os.getenv("COMPAGNON_DATA_DIR", config.data_dir)
        config.memory_dir = os.getenv("COMPAGNON_MEMORY_DIR", str(Path(config.data_dir) / "memory"))
        config.session_dir = os.getenv("COMPAGNON_SESSION_DIR", str(Path(config.data_dir) / "sessions"))
        config.daily_budget_usd = float(os.getenv("COMPAGNON_DAILY_BUDGET", str(config.daily_budget_usd)))

        # Local LLM provider
        config.provider = os.getenv("COMPAGNON_PROVIDER", "anthropic" if config.anthropic_api_key else "local")
        config.local_base_url = os.getenv("COMPAGNON_LOCAL_URL", config.local_base_url)
        config.local_api_key = os.getenv("COMPAGNON_LOCAL_API_KEY", config.local_api_key)
        config.local_model = os.getenv("COMPAGNON_LOCAL_MODEL", config.local_model)

        for d in [config.data_dir, config.memory_dir, config.session_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)
        return config


MCP_PRESETS = {
    "filesystem": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{work_dir}"],
        "transport": "stdio",
    },
    "github": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{github_token}"}, "transport": "stdio",
    },
    "git": {
        "command": "uvx", "args": ["mcp-server-git", "--repository", "{work_dir}"],
        "transport": "stdio",
    },
    "fetch": {
        "command": "uvx", "args": ["mcp-server-fetch"], "transport": "stdio",
    },
    "postgres": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "{postgres_url}"],
        "transport": "stdio",
    },
    "sqlite": {
        "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "{db_path}"],
        "transport": "stdio",
    },
    "brave-search": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "{brave_key}"}, "transport": "stdio",
    },
    "slack": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "{slack_token}"}, "transport": "stdio",
    },
    "puppeteer": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "transport": "stdio",
    },
}
