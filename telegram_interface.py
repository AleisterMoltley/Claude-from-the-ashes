"""
Telegram Interface v2 — Streaming updates, session persistence, cost tracking.
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
import time
import uuid
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatAction

from config import CompagnonConfig, MCP_PRESETS
from tool_registry import ToolRegistry, ToolContext
from query_engine import QueryEngine, StreamEvent
from token_tracker import CostTracker, TokenUsage
from session_store import Session, SessionStore
from memory.memory import MemoryStore, MemoryReadTool, MemoryWriteTool
from tools.bash_tool import BashTool
from tools.file_read import FileReadTool
from tools.file_tools import FileWriteTool, FileEditTool
from tools.web_tools import WebSearchTool, WebFetchTool
from tools.agent_tool import AgentTool
from tools.mcp_tool import MCPManager, MCPServerConfig

logger = logging.getLogger(__name__)
MAX_TG_LEN = 4000
TOOL_ICONS = {"bash": "💻", "file_read": "📖", "file_write": "📝", "file_edit": "✂️",
              "web_search": "🔍", "web_fetch": "🌐", "agent": "🤖", "memory_read": "🧠",
              "memory_write": "💾"}


class CompagnonBot:
    def __init__(self, config: CompagnonConfig):
        self.config = config
        self.registry = ToolRegistry()
        self.memory_store = MemoryStore(config.memory_dir)
        self.session_store = SessionStore(config.session_dir)
        self.cost_tracker = CostTracker(config.data_dir)
        self.mcp_manager = MCPManager()
        self._sessions: dict[int, Session] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._setup_tools()

    def _setup_tools(self):
        self.registry.register(BashTool())
        self.registry.register(FileReadTool())
        self.registry.register(FileWriteTool())
        self.registry.register(FileEditTool())
        self.registry.register(WebSearchTool())
        self.registry.register(WebFetchTool())
        self.registry.register(MemoryReadTool(self.memory_store))
        self.registry.register(MemoryWriteTool(self.memory_store))

        def factory():
            return QueryEngine(config=self.config, registry=self.registry,
                               memory_context=self.memory_store.get_prompt_context(),
                               cost_tracker=self.cost_tracker)
        self.registry.register(AgentTool(query_engine_factory=factory))

    async def _setup_mcp(self):
        for name, cfg in self.config.mcp_servers.items():
            sc = MCPServerConfig(name=name, command=cfg.get("command"), args=cfg.get("args", []),
                                 env=cfg.get("env", {}), url=cfg.get("url"), transport=cfg.get("transport", "stdio"))
            for tool in await self.mcp_manager.connect_server(sc):
                self.registry.register_mcp(tool)

    def _get_session(self, uid: int) -> Session:
        if uid not in self._sessions:
            # Try loading from disk
            saved = self.session_store.load_latest(uid)
            if saved:
                self._sessions[uid] = saved
            else:
                self._sessions[uid] = Session(user_id=uid, working_dir=self.config.working_dir)
        return self._sessions[uid]

    def _save_session(self, session: Session):
        self.session_store.save(session)
        self.session_store.cleanup_old(session.user_id)

    def _authorized(self, uid: int) -> bool:
        return not self.config.telegram_allowed_users or uid in self.config.telegram_allowed_users

    # ── Commands ──────────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        daily = self.cost_tracker.format_daily_summary()
        await update.message.reply_text(
            f"🤖 <b>Compagnon v2</b>\n\n"
            f"Session: <code>{s.session_id}</code>\n"
            f"Model: <code>{self.config.model}</code>\n"
            f"Tools: {len(self.registry.list_names())}\n"
            f"Dir: <code>{s.working_dir}</code>\n"
            f"💰 {daily}\n\n"
            f"/clear — New session\n/status — Info\n/cd — Change dir\n"
            f"/model — Switch model\n/tools — List tools\n/memory — Memories\n"
            f"/auto — Toggle auto-approve\n/sessions — Past sessions\n"
            f"/resume &lt;id&gt; — Resume session\n/budget — Cost info\n"
            f"/mcp — MCP servers",
            parse_mode=ParseMode.HTML)

    async def cmd_clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        self._save_session(s)  # Save old session
        s.clear()
        await update.message.reply_text(f"🗑️ New session: <code>{s.session_id}</code>", parse_mode=ParseMode.HTML)

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        from auto_compact import estimate_token_count
        from config import get_autocompact_threshold
        tokens = estimate_token_count(s.messages)
        threshold = get_autocompact_threshold(self.config.model)
        pct = (tokens / threshold * 100) if threshold > 0 else 0
        await update.message.reply_text(
            f"📊 <b>Status</b>\n"
            f"Session: <code>{s.session_id}</code> ({len(s.messages)} msgs)\n"
            f"Context: ~{tokens:,}/{threshold:,} tokens ({pct:.0f}%)\n"
            f"Dir: <code>{s.working_dir}</code>\n"
            f"Model: <code>{self.config.model}</code>\n"
            f"Cost: ${s.total_cost_usd:.4f} | Tools: {s.tool_calls}\n"
            f"Compactions: {s.compactions}\n"
            f"Auto-approve: {'⚡ ON' if self.config.auto_approve_write else '🛡️ OFF'}\n"
            f"💰 {self.cost_tracker.format_daily_summary()}",
            parse_mode=ParseMode.HTML)

    async def cmd_cd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        args = update.message.text.split(maxsplit=1)
        if len(args) < 2:
            await update.message.reply_text(f"📂 <code>{s.working_dir}</code>", parse_mode=ParseMode.HTML); return
        d = os.path.realpath(os.path.expanduser(args[1].strip()) if os.path.isabs(args[1].strip()) else os.path.join(s.working_dir, args[1].strip()))
        if os.path.isdir(d):
            s.working_dir = d; await update.message.reply_text(f"📂 <code>{d}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"❌ Not a directory: {d}")

    async def cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        args = update.message.text.split(maxsplit=1)
        if len(args) < 2:
            await update.message.reply_text(f"Model: <code>{self.config.model}</code>", parse_mode=ParseMode.HTML); return
        self.config.model = args[1].strip()
        await update.message.reply_text(f"✅ Model: <code>{self.config.model}</code>", parse_mode=ParseMode.HTML)

    async def cmd_tools(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        tools = self.registry.get_all_enabled()
        lines = [f"🔧 <b>{len(tools)} tools:</b>\n"]
        for t in tools:
            icon = TOOL_ICONS.get(t.name, "🔧" if not t.is_read_only else "📖")
            lines.append(f"{icon} <code>{t.name}</code>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        m = self.memory_store.list_all()
        if not m: await update.message.reply_text("📭 No memories."); return
        lines = [f"🧠 <b>{len(m)} memories:</b>\n"]
        for x in m:
            lines.append(f"• <b>{html.escape(x['key'])}</b> [{','.join(x.get('tags',[]))}]")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_auto(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        self.config.auto_approve_write = not self.config.auto_approve_write
        self.config.auto_approve_bash_destructive = self.config.auto_approve_write
        st = "ON ⚡" if self.config.auto_approve_write else "OFF 🛡️"
        await update.message.reply_text(f"Auto-approve: <b>{st}</b>", parse_mode=ParseMode.HTML)

    async def cmd_sessions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        sessions = self.session_store.list_sessions(update.effective_user.id, limit=10)
        if not sessions: await update.message.reply_text("No past sessions."); return
        lines = [f"📋 <b>Sessions:</b>\n"]
        for s in sessions:
            import datetime
            ts = datetime.datetime.fromtimestamp(s["updated_at"]).strftime("%m/%d %H:%M") if s["updated_at"] else "?"
            lines.append(f"• <code>{s['session_id']}</code> {ts} — {s['messages']} msgs, ${s['cost_usd']:.4f}")
        lines.append(f"\n/resume &lt;id&gt; to continue a session")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        args = update.message.text.split(maxsplit=1)
        if len(args) < 2: await update.message.reply_text("Usage: /resume <session_id>"); return
        sid = args[1].strip()
        loaded = self.session_store.load(update.effective_user.id, sid)
        if not loaded: await update.message.reply_text(f"Session {sid} not found."); return
        self._sessions[update.effective_user.id] = loaded
        await update.message.reply_text(
            f"▶️ Resumed <code>{sid}</code> ({len(loaded.messages)} msgs, ${loaded.total_cost_usd:.4f})",
            parse_mode=ParseMode.HTML)

    async def cmd_budget(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        t = self.cost_tracker.today
        pct = (t.total_cost_usd / self.config.daily_budget_usd * 100) if self.config.daily_budget_usd > 0 else 0
        bar_len = 20
        filled = int(pct / 100 * bar_len)
        bar = "█" * min(filled, bar_len) + "░" * max(0, bar_len - filled)
        await update.message.reply_text(
            f"💰 <b>Budget</b>\n\n"
            f"[{bar}] {pct:.1f}%\n"
            f"${t.total_cost_usd:.4f} / ${self.config.daily_budget_usd:.2f}\n\n"
            f"API calls: {t.api_calls}\n"
            f"Tool calls: {t.tool_calls}\n"
            f"Tokens: ↓{t.total_input:,} ↑{t.total_output:,}",
            parse_mode=ParseMode.HTML)

    async def cmd_mcp(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        lines = ["🔌 <b>MCP Server Presets:</b>\n"]
        for name in MCP_PRESETS:
            lines.append(f"• <code>{name}</code>")
        lines.append(f"\nActive: {list(self.config.mcp_servers.keys()) or 'none'}")
        lines.append(f"\nSet COMPAGNON_MCP_SERVERS env var to activate.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # ── Message Handler with Streaming ───────────────────────────

    async def handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        user_msg = update.message.text or ""
        if not user_msg.strip(): return

        session = self._get_session(update.effective_user.id)
        chat_id = update.effective_chat.id
        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

        # Live status message for streaming updates
        status_msg = await update.message.reply_text("⏳ Working...")
        display_lines: list[str] = []
        text_buffer = ""
        last_edit_time = 0.0
        tool_count = 0

        async def update_status(force: bool = False):
            nonlocal last_edit_time
            now = time.time()
            if not force and (now - last_edit_time) < self.config.stream_update_interval:
                return
            last_edit_time = now

            content = "\n".join(display_lines[-30:])  # Keep last 30 lines
            if len(content) > MAX_TG_LEN:
                content = content[-MAX_TG_LEN:]
            if not content.strip():
                content = "⏳ Working..."
            try:
                await status_msg.edit_text(content, parse_mode=ParseMode.HTML)
            except Exception:
                try:
                    await status_msg.edit_text(content)  # Fallback without HTML
                except Exception:
                    pass

        # Permission callback
        async def permission_cb(tool_name: str, params: dict) -> bool:
            cid = uuid.uuid4().hex[:8]
            future = asyncio.get_event_loop().create_future()
            self._pending[cid] = future
            preview = json.dumps(params, ensure_ascii=False)
            if len(preview) > 500: preview = preview[:500] + "..."
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅", callback_data=f"y_{cid}"),
                InlineKeyboardButton("❌", callback_data=f"n_{cid}"),
            ]])
            await ctx.bot.send_message(chat_id,
                f"⚠️ <code>{html.escape(tool_name)}</code>\n<pre>{html.escape(preview)}</pre>",
                parse_mode=ParseMode.HTML, reply_markup=kb)
            try:
                return await asyncio.wait_for(future, timeout=300)
            except asyncio.TimeoutError:
                self._pending.pop(cid, None); return False

        engine = QueryEngine(config=self.config, registry=self.registry,
                             memory_context=self.memory_store.get_prompt_context(),
                             cost_tracker=self.cost_tracker)

        tool_ctx = ToolContext(working_dir=session.working_dir, config=self.config,
                               session_id=session.session_id, permission_callback=permission_cb)

        session.messages.append({"role": "user", "content": user_msg})

        try:
            async for event in engine.query_streaming(session.messages, tool_ctx):
                if event.type == "text":
                    text_buffer += event.text

                elif event.type == "tool_call":
                    tool_count += 1
                    icon = TOOL_ICONS.get(event.tool_name, "🔧")
                    param_preview = json.dumps(event.tool_params, ensure_ascii=False)
                    if len(param_preview) > 80: param_preview = param_preview[:80] + "…"
                    display_lines.append(f"{icon} <code>{html.escape(event.tool_name)}</code> {html.escape(param_preview)}")
                    await update_status()

                elif event.type == "tool_result":
                    tr = event.tool_result
                    status = "✅" if not tr.is_error else "❌"
                    preview = (tr.output or tr.error)[:80].replace("\n", " ")
                    display_lines.append(f"  {status} {html.escape(preview)}")
                    await update_status()

                elif event.type == "compact":
                    display_lines.append(f"📦 {event.text}")
                    session.compactions += 1
                    await update_status(force=True)

                elif event.type == "error":
                    display_lines.append(f"❌ {html.escape(event.text)}")
                    await update_status(force=True)

                elif event.type == "done":
                    if event.usage:
                        session.total_input_tokens += event.usage.input_tokens
                        session.total_output_tokens += event.usage.output_tokens
                        session.total_cost_usd += event.usage.cost_usd(self.config.model)
                    session.tool_calls += tool_count

            # Build final response
            try: await status_msg.delete()
            except Exception: pass

            parts = []
            if display_lines:
                parts.append("\n".join(display_lines))
                parts.append("")
            if text_buffer.strip():
                parts.append(text_buffer.strip())

            # Footer
            cost = session.total_cost_usd
            daily = self.cost_tracker.today.total_cost_usd
            parts.append(f"\n<i>🔧 {tool_count} tools | 💰 ${cost:.4f} session / ${daily:.4f} today</i>")

            response = "\n".join(parts)
            await self._send_long(ctx.bot, chat_id, response)

            # Auto-title on first exchange
            if not session.title and len(session.messages) >= 2:
                session.title = user_msg[:60]

            # Persist session
            self._save_session(session)

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            try: await status_msg.edit_text(f"❌ {e}")
            except: await ctx.bot.send_message(chat_id, f"❌ {e}")

    async def _send_long(self, bot, chat_id: int, text: str):
        if len(text) <= MAX_TG_LEN:
            try: await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except: await bot.send_message(chat_id, text)
            return
        while text:
            if len(text) <= MAX_TG_LEN:
                chunk, text = text, ""
            else:
                split = text.rfind("\n", 0, MAX_TG_LEN)
                if split < MAX_TG_LEN // 2: split = MAX_TG_LEN
                chunk, text = text[:split], text[split:].lstrip("\n")
            try: await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)
            except: await bot.send_message(chat_id, chunk)

    # ── Callback (permission buttons) ────────────────────────────

    async def handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        if not q.data: return
        action, cid = q.data.split("_", 1)
        fut = self._pending.pop(cid, None)
        if fut and not fut.done():
            approved = action == "y"
            fut.set_result(approved)
            await q.edit_message_text(q.message.text + f"\n\n<b>{'✅ Approved' if approved else '❌ Denied'}</b>",
                                      parse_mode=ParseMode.HTML)

    # ── Run ──────────────────────────────────────────────────────

    async def run(self):
        await self._setup_mcp()
        app = Application.builder().token(self.config.telegram_token).build()

        for cmd, handler in [
            ("start", self.cmd_start), ("clear", self.cmd_clear), ("status", self.cmd_status),
            ("cd", self.cmd_cd), ("model", self.cmd_model), ("tools", self.cmd_tools),
            ("memory", self.cmd_memory), ("auto", self.cmd_auto), ("sessions", self.cmd_sessions),
            ("resume", self.cmd_resume), ("budget", self.cmd_budget), ("mcp", self.cmd_mcp),
        ]:
            app.add_handler(CommandHandler(cmd, handler))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(CallbackQueryHandler(self.handle_callback))

        logger.info(f"Compagnon v2 — {self.config.model} — {len(self.registry.list_names())} tools")

        async with app:
            await app.start()
            await app.updater.start_polling()
            logger.info("Running. Ctrl+C to stop.")
            try:
                while True: await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                await app.updater.stop(); await app.stop()
                await self.mcp_manager.disconnect_all()
                # Save all active sessions
                for s in self._sessions.values():
                    self._save_session(s)
