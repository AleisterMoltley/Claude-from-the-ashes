# Compagnon 🤖

Autonomous AI agent controlled via Telegram. Inspired by the Claude Code architecture — tool system, agentic query loop, MCP integration, persistent memory, sub-agent spawning.

## Features

- **Agentic Loop** — Claude calls tools autonomously until the task is done
- **10 Built-in Tools** — bash, file_read, file_write, file_edit, web_search, web_fetch, memory_read, memory_write, agent (sub-agents)
- **MCP Integration** — Connect any MCP server (stdio or SSE) and its tools become available
- **Permission System** — Safe commands auto-approve, dangerous ones ask via inline buttons
- **Persistent Memory** — Save/search/recall information across sessions
- **Sub-Agent Spawning** — Break complex tasks into isolated subtasks
- **Telegram Control** — Full control via Telegram with inline permission approvals

## Quick Start

```bash
# Clone
git clone https://github.com/AleisterMoltley/Compagnon.git
cd Compagnon

# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY and TELEGRAM_TOKEN

# Run
python main.py
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show status and help |
| `/clear` | Clear conversation, new session |
| `/status` | Current session info |
| `/cd <path>` | Change working directory |
| `/model <name>` | Switch Claude model |
| `/tools` | List available tools |
| `/memory` | Show stored memories |
| `/auto` | Toggle auto-approve mode |

## MCP Servers

Add MCP servers via env var:

```json
COMPAGNON_MCP_SERVERS={
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"],
    "transport": "stdio"
  },
  "github": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {"GITHUB_TOKEN": "ghp_..."},
    "transport": "stdio"
  }
}
```

## Railway Deployment

```bash
railway login
railway init
railway link
# Set env vars in Railway dashboard
railway up
```

## Architecture

```
main.py                 → Entrypoint, config loading
telegram_interface.py   → Telegram bot, session management, permissions
query_engine.py         → Core agentic loop (system prompt → API → tool use → loop)
tool_registry.py        → Tool type system, registry, schema generation
config.py               → Configuration, safe command lists

tools/
  bash_tool.py          → Shell execution with safety checks
  file_read.py          → File reading with line ranges
  file_tools.py         → File write + file edit (str_replace)
  web_tools.py          → Web search (DuckDuckGo) + URL fetch
  agent_tool.py         → Sub-agent spawning
  mcp_tool.py           → MCP server connection + proxy tools

memory/
  memory.py             → Persistent key-value memory store
```

## How It Works

1. User sends message via Telegram
2. Message goes to `QueryEngine.chat()`
3. System prompt is built with tools, memory, and custom instructions
4. Anthropic API is called with tool definitions
5. If Claude returns `tool_use` → tool is executed → result fed back → loop
6. If tool needs confirmation → inline buttons shown in Telegram
7. When Claude stops (no more tool calls) → final text sent to user
8. Conversation history persists in session for context

The agentic loop runs until `stop_reason=end_turn` or max tool calls reached.
