"""
Token Tracker — Cost tracking and budget enforcement.
Inspired by Claude Code's cost-tracker.ts and modelCost.ts.
"""
import time
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from config import calculate_cost_usd

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, usage):
        self.input_tokens += getattr(usage, 'input_tokens', 0)
        self.output_tokens += getattr(usage, 'output_tokens', 0)
        self.cache_creation_input_tokens += getattr(usage, 'cache_creation_input_tokens', 0) or 0
        self.cache_read_input_tokens += getattr(usage, 'cache_read_input_tokens', 0) or 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost_usd(self, model: str) -> float:
        return calculate_cost_usd(
            model, self.input_tokens, self.output_tokens,
            self.cache_read_input_tokens, self.cache_creation_input_tokens,
        )

    def format_summary(self, model: str) -> str:
        cost = self.cost_usd(model)
        return (
            f"↓{self.input_tokens:,} ↑{self.output_tokens:,} "
            f"(cache: r{self.cache_read_input_tokens:,} w{self.cache_creation_input_tokens:,}) "
            f"${cost:.4f}"
        )


@dataclass
class DailyTracker:
    """Track daily token usage and enforce budget."""
    date: str = ""
    total_input: int = 0
    total_output: int = 0
    total_cost_usd: float = 0.0
    api_calls: int = 0
    tool_calls: int = 0

    def add(self, usage: TokenUsage, model: str):
        self.total_input += usage.input_tokens
        self.total_output += usage.output_tokens
        self.total_cost_usd += usage.cost_usd(model)
        self.api_calls += 1

    def is_over_budget(self, budget_usd: float) -> bool:
        return self.total_cost_usd >= budget_usd

    def is_warning(self, warn_at_usd: float) -> bool:
        return self.total_cost_usd >= warn_at_usd


class CostTracker:
    """Persistent daily cost tracking."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._today: DailyTracker = self._load_today()

    def _today_str(self) -> str:
        import datetime
        return datetime.date.today().isoformat()

    def _tracker_path(self, date_str: str) -> Path:
        return self.data_dir / f"cost_{date_str}.json"

    def _load_today(self) -> DailyTracker:
        today = self._today_str()
        path = self._tracker_path(today)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                tracker = DailyTracker(**data)
                tracker.date = today
                return tracker
            except Exception:
                pass
        return DailyTracker(date=today)

    def _save(self):
        path = self._tracker_path(self._today.date)
        path.write_text(json.dumps(self._today.__dict__))

    def record(self, usage: TokenUsage, model: str, tool_calls: int = 0):
        # Check if day rolled over
        today = self._today_str()
        if self._today.date != today:
            self._today = DailyTracker(date=today)

        self._today.add(usage, model)
        self._today.tool_calls += tool_calls
        self._save()

    @property
    def today(self) -> DailyTracker:
        today = self._today_str()
        if self._today.date != today:
            self._today = DailyTracker(date=today)
        return self._today

    def format_daily_summary(self) -> str:
        t = self.today
        return (
            f"Today: {t.api_calls} calls, {t.tool_calls} tools, "
            f"↓{t.total_input:,} ↑{t.total_output:,} = ${t.total_cost_usd:.4f}"
        )
