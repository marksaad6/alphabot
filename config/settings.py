"""
config/settings.py
------------------
Central configuration for the trading bot.
All user-configurable values live here or in settings.yaml.
"""

import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RiskConfig:
    """Risk management rules — CRITICAL to tune before going live."""

    # Max % of total portfolio to risk on ANY single trade
    max_position_size_pct: float = 0.05        # 5% per trade (e.g. $250 on $5,000 account)

    # Max total % of portfolio in open positions at once
    max_total_exposure_pct: float = 0.50       # 50% max deployed at any time

    # Stop-loss: auto-close position if it loses this much
    stop_loss_pct: float = 0.02                # 2% stop loss per trade

    # Take-profit: auto-close when this gain is hit
    take_profit_pct: float = 0.04              # 4% take profit (2:1 risk/reward)

    # Max trades per day to avoid overtrading
    max_daily_trades: int = 5

    # Minimum cash reserve — never go below this
    min_cash_reserve: float = 500.00           # Always keep $500 cash


@dataclass
class StrategyConfig:
    """Which strategies to run and their parameters."""

    # ---- Stock strategies ----
    use_momentum:       bool = True     # Buy stocks in strong uptrends
    use_mean_reversion: bool = True     # Buy oversold, sell overbought
    use_breakout:       bool = True     # Buy on volume-confirmed breakouts

    # ---- Options strategies ----
    use_covered_calls:  bool = False    # Sell calls on stocks you own (requires 100 shares)
    use_cash_secured_puts: bool = True  # Sell puts on stocks you want to own (theta strategy)
    use_long_calls:     bool = False    # Buy calls for directional bets (higher risk)

    # ---- AI filtering ----
    use_ai_filter: bool = True          # Use Claude AI to validate signals before trading
    ai_confidence_threshold: float = 0.70  # Only trade if AI is 70%+ confident

    # ---- Watchlist: symbols to trade ----
    stock_watchlist: list = field(default_factory=lambda: [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
        "AMZN", "GOOGL", "META", "TSLA", "AMD"
    ])

    # Minimum average daily volume to consider (filters out illiquid stocks)
    min_avg_volume: int = 1_000_000


@dataclass
class SchwabConfig:
    """Schwab API credentials and endpoints."""

    # These come from environment variables — NEVER hardcode credentials
    app_key: str = field(default_factory=lambda: os.getenv("SCHWAB_APP_KEY", ""))
    app_secret: str = field(default_factory=lambda: os.getenv("SCHWAB_APP_SECRET", ""))
    callback_url: str = field(default_factory=lambda: os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1"))

    # Account number (find in Schwab dashboard)
    account_number: str = field(default_factory=lambda: os.getenv("SCHWAB_ACCOUNT_NUMBER", ""))

    # Token storage path (schwabdev manages token refresh automatically)
    token_path: str = "config/tokens.json"


@dataclass
class AIConfig:
    """Claude AI configuration for signal analysis."""

    # Anthropic API key
    api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    # Model to use for trade analysis
    model: str = "claude-sonnet-4-20250514"

    # How many recent news/price points to include in AI analysis
    context_lookback_days: int = 5


class Settings:
    """
    Main settings class.
    Loads from settings.yaml if it exists, then applies environment variable overrides.
    """

    def __init__(self, config_path: str = "config/settings.yaml", mode: str = "paper"):
        self.mode = mode
        self.is_paper = (mode == "paper")
        self.is_live = (mode == "live")
        self.is_backtest = (mode == "backtest")

        # Load sub-configs
        self.schwab = SchwabConfig()
        self.risk = RiskConfig()
        self.strategy = StrategyConfig()
        self.ai = AIConfig()

        # Override with yaml file if it exists
        config_file = Path(config_path)
        if config_file.exists():
            self._load_yaml(config_file)

        self._validate()

    def _load_yaml(self, path: Path):
        """Load settings from yaml config file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        if "risk" in data:
            for k, v in data["risk"].items():
                if hasattr(self.risk, k):
                    setattr(self.risk, k, v)

        if "strategy" in data:
            for k, v in data["strategy"].items():
                if hasattr(self.strategy, k):
                    setattr(self.strategy, k, v)

    def _validate(self):
        """Check that critical settings are present before starting."""
        if self.is_paper:
            if not self.schwab.account_number:
                raise ValueError("SCHWAB_ACCOUNT_NUMBER not set")
            print("=" * 60)
            print("  PAPER MODE - confirm this is your paperMoney account:")
            print(f"  Account: ...{self.schwab.account_number[-4:]}")
            print("=" * 60)
        if self.is_live:
            errors = []
            if not self.schwab.app_key:
                errors.append("SCHWAB_APP_KEY environment variable not set")
            if not self.schwab.app_secret:
                errors.append("SCHWAB_APP_SECRET environment variable not set")
            if not self.schwab.account_number:
                errors.append("SCHWAB_ACCOUNT_NUMBER environment variable not set")
            if errors:
                raise ValueError(
                    "Missing required credentials for live trading:\n" +
                    "\n".join(f"  - {e}" for e in errors)
                )

    def __repr__(self):
        return (
            f"Settings(mode={self.mode}, "
            f"strategies={[k for k,v in vars(self.strategy).items() if v is True]}, "
            f"watchlist_size={len(self.strategy.stock_watchlist)})"
        )
