# Compagnon v2 🤖

An autonomous AI agent controlled via Telegram, powered by Anthropic's Claude. Inspired by the Claude Code architecture — featuring an agentic tool-use loop, MCP integration, persistent memory, and sub-agent spawning.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [Option A: Run Locally with Python](#option-a-run-locally-with-python)
  - [Option B: Run with Docker](#option-b-run-with-docker)
  - [Option C: Deploy to Railway](#option-c-deploy-to-railway)
- [Configuration](#configuration)
- [Telegram Commands](#telegram-commands)
- [MCP Server Integration](#mcp-server-integration)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Troubleshooting](#troubleshooting)

## Features

- **Agentic Loop** — Claude autonomously calls tools in a loop until the task is complete
- **10 Built-in Tools** — `bash`, `file_read`, `file_write`, `file_edit`, `web_search`, `web_fetch`, `memory_read`, `memory_write`, `agent` (sub-agents)
- **MCP Integration** — Connect any MCP server (stdio or SSE) and its tools become available automatically
- **Permission System** — Safe/read-only commands auto-approve; dangerous ones prompt the user via Telegram inline buttons
- **Persistent Memory** — Save, search, and recall information across sessions
- **Sub-Agent Spawning** — Break complex tasks into isolated subtasks handled by child agents
- **Telegram Control** — Full control via a Telegram bot with inline permission approvals
- **Auto-Compact** — Automatic context summarization when the conversation approaches token limits
- **Cost Tracking** — Per-session and daily cost tracking with configurable budget limits
- **Session Persistence** — Conversations are saved to disk and restored on restart

## Prerequisites

Before you begin, make sure you have:

1. **Python 3.12+** — [Download Python](https://www.python.org/downloads/)
2. **An Anthropic API Key** — Sign up at [console.anthropic.com](https://console.anthropic.com/) and create an API key (starts with `sk-ant-...`)
3. **A Telegram Bot Token** — Talk to [@BotFather](https://t.me/BotFather) on Telegram:
   - Send `/newbot` and follow the prompts to create a new bot
   - Copy the bot token (looks like `123456789:ABCdefGhIJKlmNoPQRsTUVwxYZ`)
4. **Your Telegram User ID** *(optional, for access restriction)* — Talk to [@userinfobot](https://t.me/userinfobot) to find your numeric Telegram user ID

## Getting Started

### Option A: Run Locally with Python

```bash
# 1. Clone the repository
git clone https://github.com/AleisterMoltley/Claude-from-the-ashes.git
cd Claude-from-the-ashes

# 2. (Recommended) Create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your configuration file
cp .env.example .env

# 5. Edit .env and fill in your keys (see Configuration section below)
#    At minimum, set ANTHROPIC_API_KEY and TELEGRAM_TOKEN

# 6. Start the bot
python main.py
```

#### Command-line Options

```
python main.py [OPTIONS]

  --model MODEL        Override the Claude model (e.g. claude-opus-4-20250514)
  --work-dir PATH      Set the working directory for file operations
  --auto-approve       Auto-approve all tool calls (skip confirmation prompts)
  --budget AMOUNT      Set daily spending budget in USD
  --debug              Enable debug-level logging
```

### Option B: Run with Docker

```bash
# 1. Clone the repository
git clone https://github.com/AleisterMoltley/Claude-from-the-ashes.git
cd Claude-from-the-ashes

# 2. Create your configuration file
cp .env.example .env
# Edit .env with your API keys

# 3. Build the Docker image
docker build -t compagnon .

# 4. Run the container
docker run -d \
  --name compagnon \
  --env-file .env \
  --restart unless-stopped \
  compagnon
```

The Docker image is based on `python:3.12-slim` and includes `git`, `curl`, `jq`, `ripgrep`, `tree`, `nodejs`, and `npm` for full tool support.

### Option C: Deploy to Railway

[Railway](https://railway.app/) is a cloud platform that can host this bot 24/7.

```bash
# 1. Install the Railway CLI
npm install -g @railway/cli

# 2. Log in
railway login

# 3. Initialize a new project
railway init

# 4. Link to the project
railway link

# 5. Set environment variables in the Railway dashboard:
#    - ANTHROPIC_API_KEY
#    - TELEGRAM_TOKEN
#    - (optional) TELEGRAM_ALLOWED_USERS, COMPAGNON_MODEL, etc.

# 6. Deploy
railway up
```

## Configuration

Copy `.env.example` to `.env` and edit it. The following environment variables are supported:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | **Yes** | — | Your Anthropic API key (`sk-ant-...`) |
| `TELEGRAM_TOKEN` | **Yes** | — | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOWED_USERS` | No | *(all users)* | Comma-separated Telegram user IDs to restrict access |
| `COMPAGNON_MODEL` | No | `claude-sonnet-4-20250514` | Claude model to use |
| `COMPAGNON_WORK_DIR` | No | `$HOME` | Default working directory for file/bash operations |
| `COMPAGNON_DATA_DIR` | No | `~/.compagnon` | Directory for sessions, memory, and cost data |
| `COMPAGNON_MEMORY_DIR` | No | `<data_dir>/memory` | Directory for persistent memory files |
| `COMPAGNON_DAILY_BUDGET` | No | `10.0` | Daily spending limit in USD |
| `COMPAGNON_INSTRUCTIONS_FILE` | No | — | Path to a text file with custom system instructions |
| `COMPAGNON_MCP_SERVERS` | No | — | JSON config for MCP servers (see below) |

### Minimal `.env` Example

```bash
ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
TELEGRAM_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxYZ
```

### Restricting Access

To limit bot access to specific Telegram users only, set `TELEGRAM_ALLOWED_USERS` to a comma-separated list of numeric Telegram user IDs:

```bash
TELEGRAM_ALLOWED_USERS=12345678,87654321
```

## Telegram Commands

Once the bot is running, open your Telegram bot and use these commands:

| Command | Description |
|---------|-------------|
| `/start` | Show status and help |
| `/clear` | Clear conversation history and start a new session |
| `/status` | Show current session info (tokens, cost, model) |
| `/cd <path>` | Change the working directory |
| `/model <name>` | Switch to a different Claude model |
| `/tools` | List all available tools |
| `/memory` | Show stored memories |
| `/auto` | Toggle auto-approve mode for all tool calls |

**Regular messages** are sent directly to Claude. The agent will reason about your request and autonomously use tools (run commands, read/write files, search the web, etc.) until the task is done. If a tool call requires confirmation, you'll see inline ✅/❌ buttons.

## MCP Server Integration

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers can extend the bot with additional tools. Configure them via the `COMPAGNON_MCP_SERVERS` environment variable as a JSON object:

```bash
COMPAGNON_MCP_SERVERS={"filesystem":{"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/home/user"],"transport":"stdio"},"github":{"command":"npx","args":["-y","@modelcontextprotocol/server-github"],"env":{"GITHUB_PERSONAL_ACCESS_TOKEN":"ghp_..."},"transport":"stdio"}}
```

### Formatted Example

```json
{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"],
    "transport": "stdio"
  },
  "github": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..." },
    "transport": "stdio"
  }
}
```

Each MCP server entry supports:
- `command` — The executable to run (e.g. `npx`, `uvx`)
- `args` — Command-line arguments
- `env` — Additional environment variables
- `url` — URL for SSE transport
- `transport` — `"stdio"` or `"sse"`

### Built-in MCP Presets

The following presets are available in `config.py` and can be used as references: `filesystem`, `github`, `git`, `fetch`, `postgres`, `sqlite`, `brave-search`, `slack`, `puppeteer`.

## Architecture

```
main.py                 → Entrypoint, argument parsing, config loading
telegram_interface.py   → Telegram bot, streaming UI updates, session management, permissions
query_engine.py         → Core agentic loop (system prompt → Claude API → tool use → loop)
tool_registry.py        → Tool base class, registry, API schema generation
config.py               → Configuration, model pricing, safe command lists, MCP presets
auto_compact.py         → Automatic context summarization when approaching token limits
token_tracker.py        → Per-turn and daily cost tracking with budget enforcement
session_store.py        → File-based session persistence (save/restore conversations)

tools/
  bash_tool.py          → Shell command execution with safety checks
  file_read.py          → File reading with optional line ranges
  file_tools.py         → File write (create/overwrite) + file edit (str_replace)
  web_tools.py          → Web search (DuckDuckGo) + URL content fetch
  agent_tool.py         → Sub-agent spawning for isolated subtasks
  mcp_tool.py           → MCP server connection manager + proxy tools

memory/
  memory.py             → Persistent key-value memory store with search
```

## How It Works

1. **User sends a message** via Telegram
2. The message is passed to `QueryEngine.chat()`
3. A **system prompt** is built including available tools, memory context, and custom instructions
4. The **Anthropic API** is called with tool definitions (streaming)
5. If Claude returns `tool_use` blocks → the tools are executed → results are fed back → the loop continues
6. If a tool requires **confirmation** → inline ✅/❌ buttons are shown in Telegram
7. When Claude stops calling tools (`stop_reason=end_turn`) → the final text response is sent to the user
8. **Conversation history** persists in the session (on disk) for continuity
9. When the context grows too large → **auto-compact** summarizes the conversation to free up tokens

The agentic loop runs until `stop_reason=end_turn` or the maximum number of tool calls per turn is reached (default: 50).

## Troubleshooting

### Common Issues

| Problem | Solution |
|---------|----------|
| `ANTHROPIC_API_KEY not set` | Make sure your `.env` file exists and contains a valid `ANTHROPIC_API_KEY` |
| `TELEGRAM_TOKEN not set` | Make sure your `.env` file contains a valid Telegram bot token |
| `duckduckgo-search not installed` | Run `pip install -r requirements.txt` again |
| `mcp package not installed` | Run `pip install -r requirements.txt` — the `mcp` package is required for MCP server support |
| Bot doesn't respond | Check that `TELEGRAM_ALLOWED_USERS` is either empty (allows all) or includes your Telegram user ID |
| Budget exceeded | The daily budget is enforced per-day; wait until the next day or increase `COMPAGNON_DAILY_BUDGET` |
| Docker: tools like `git` not working | The Dockerfile includes common tools; add more with `apt-get install` in the Dockerfile if needed |

### Logs

The bot logs to stdout. For more detail, use the `--debug` flag:

```bash
python main.py --debug
```

### Data Storage

By default, all data is stored under `~/.compagnon/`:
- `~/.compagnon/memory/` — Persistent memory files
- `~/.compagnon/sessions/` — Saved conversation sessions
- `~/.compagnon/cost_*.json` — Daily cost tracking data

## License

This project is provided as-is. See the repository for license details.
