"""Compagnon v2 — Autonomous AI Agent, Telegram-controlled."""
import asyncio
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CompagnonConfig
from telegram_interface import CompagnonBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("compagnon")


def main():
    parser = argparse.ArgumentParser(description="Compagnon v2 — Autonomous AI Agent")
    parser.add_argument("--model", help="Anthropic model")
    parser.add_argument("--work-dir", help="Working directory")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    parser.add_argument("--budget", type=float, help="Daily budget in USD")
    parser.add_argument("--local", action="store_true", help="Use local LLM (Ollama)")
    parser.add_argument("--local-model", help="Local model name (e.g. qwen2.5:32b)")
    parser.add_argument("--local-url", help="Local LLM API URL")
    args = parser.parse_args()

    if args.debug: logging.getLogger().setLevel(logging.DEBUG)

    config = CompagnonConfig.from_env()
    if args.model: config.model = args.model
    if args.work_dir: config.working_dir = os.path.realpath(args.work_dir)
    if args.auto_approve:
        config.auto_approve_write = True; config.auto_approve_bash_destructive = True
    if args.budget: config.daily_budget_usd = args.budget
    if args.local: config.provider = "local"
    if args.local_model: config.local_model = args.local_model
    if args.local_url: config.local_base_url = args.local_url

    # Validate based on provider
    if not config.is_local and not config.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not set (use COMPAGNON_PROVIDER=local for local LLM)")
        sys.exit(1)
    if not config.telegram_token:
        logger.error("TELEGRAM_TOKEN not set")
        sys.exit(1)

    mcp_json = os.getenv("COMPAGNON_MCP_SERVERS")
    if mcp_json:
        import json
        try: config.mcp_servers = json.loads(mcp_json)
        except json.JSONDecodeError as e: logger.error(f"Bad MCP JSON: {e}")

    instructions_file = os.getenv("COMPAGNON_INSTRUCTIONS_FILE")
    if instructions_file and os.path.exists(instructions_file):
        config.custom_instructions = open(instructions_file).read()

    logger.info(f"Compagnon v2 starting")
    logger.info(f"  Provider: {config.provider}")
    logger.info(f"  Model: {config.active_model}")
    if config.is_local:
        logger.info(f"  Local URL: {config.local_base_url}")
    logger.info(f"  Dir: {config.working_dir}")
    logger.info(f"  Budget: ${config.daily_budget_usd}/day")
    logger.info(f"  MCP: {list(config.mcp_servers.keys()) or 'none'}")

    bot = CompagnonBot(config)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
