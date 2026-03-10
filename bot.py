"""
src/bot.py
----------
The main TradingBot class.
This is the "brain" that orchestrates everything:
  1. Connects to Schwab
  2. Runs strategies to find trade signals
  3. Sends signals to AI for validation
  4. Executes approved trades
  5. Manages open positions (stop-loss, take-profit)
  6. Runs on a schedule (checks market every N minutes)
"""

import logging
import time
import schedule
from datetime import datetime, time as dt_time
import pytz

from config.settings import Settings
from src.schwab_client import SchwabClient
from src.ai.analyzer import AIAnalyzer
from src.strategies.momentum import MomentumStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.options_theta import ThetaOptionsStrategy
from src.risk_manager import RiskManager
from src.portfolio import Portfolio
from src.utils.market_hours import is_market_open, next_market_open


logger = logging.getLogger(__name__)


class TradingBot:
    """
    AlphaBot - Main orchestrator.

    The bot runs on a loop and does the following each cycle:
        - Check if market is open
        - Scan watchlist for signals
        - Validate signals with AI
        - Apply risk management rules
        - Execute approved trades
        - Monitor and manage open positions
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.running = False

        logger.info("Initializing bot components...")

        # Core components
        self.client = SchwabClient(settings.schwab, paper=settings.is_paper)
        self.portfolio = Portfolio(self.client)
        self.risk_manager = RiskManager(settings.risk, self.portfolio)
        self.ai = AIAnalyzer(settings.ai)

        # Load active strategies based on config
        self.strategies = self._load_strategies()
        logger.info(f"Loaded {len(self.strategies)} strategies: "
                    f"{[s.__class__.__name__ for s in self.strategies]}")

        # Track daily stats
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0

    def _load_strategies(self):
        """Instantiate strategies based on settings."""
        strategies = []
        cfg = self.settings.strategy

        if cfg.use_momentum:
            strategies.append(MomentumStrategy(cfg))
        if cfg.use_mean_reversion:
            strategies.append(MeanReversionStrategy(cfg))
        if cfg.use_cash_secured_puts:
            strategies.append(ThetaOptionsStrategy(cfg))

        return strategies

    def run(self):
        """Main run loop. Schedules jobs and blocks until stopped."""
        self.running = True
        logger.info("Bot is running. Press Ctrl+C to stop.")

        # Schedule the main scan cycle every 5 minutes during market hours
        schedule.every(5).minutes.do(self._run_cycle)

        # Schedule daily reset at market open
        schedule.every().day.at("09:30").do(self._daily_reset)

        # Schedule end-of-day position review
        schedule.every().day.at("15:45").do(self._end_of_day_review)

        # Run one cycle immediately on startup
        self._run_cycle()

        # Keep running
        while self.running:
            schedule.run_pending()
            time.sleep(30)  # Check schedule every 30 seconds

    def _run_cycle(self):
        """
        One full scan-signal-execute cycle.
        Called every 5 minutes during market hours.
        """
        # ── Step 1: Market hours check ─────────────────────────────
        if not is_market_open():
            next_open = next_market_open()
            logger.debug(f"Market closed. Next open: {next_open}")
            return

        logger.info("─── Running scan cycle ───────────────────────────────")

        # ── Step 2: Refresh portfolio state ────────────────────────
        self.portfolio.refresh()
        logger.info(f"Portfolio: ${self.portfolio.cash:.2f} cash | "
                    f"{len(self.portfolio.positions)} open positions")

        # ── Step 3: Check daily trade limit ────────────────────────
        if self.daily_trades >= self.settings.risk.max_daily_trades:
            logger.info(f"Daily trade limit ({self.settings.risk.max_daily_trades}) reached. "
                       f"Skipping new entries.")
            self._manage_positions()  # Still manage existing positions
            return

        # ── Step 4: Run strategies → collect raw signals ───────────
        all_signals = []
        for strategy in self.strategies:
            try:
                signals = strategy.scan(
                    symbols=self.settings.strategy.stock_watchlist,
                    client=self.client
                )
                all_signals.extend(signals)
                logger.info(f"{strategy.__class__.__name__}: {len(signals)} signals")
            except Exception as e:
                logger.error(f"Strategy {strategy.__class__.__name__} error: {e}")

        if not all_signals:
            logger.info("No signals this cycle.")
            self._manage_positions()
            return

        # ── Step 5: AI validation ──────────────────────────────────
        validated_signals = []
        if self.settings.strategy.use_ai_filter:
            for signal in all_signals:
                analysis = self.ai.analyze_signal(signal, self.client)
                if analysis.confidence >= self.settings.strategy.ai_confidence_threshold:
                    signal.ai_confidence = analysis.confidence
                    signal.ai_reasoning = analysis.reasoning
                    validated_signals.append(signal)
                    logger.info(f"✅ AI approved {signal.symbol} ({signal.direction}) "
                               f"— confidence: {analysis.confidence:.0%}")
                else:
                    logger.info(f"❌ AI rejected {signal.symbol} "
                               f"— confidence: {analysis.confidence:.0%}")
        else:
            validated_signals = all_signals

        # ── Step 6: Risk management filter ─────────────────────────
        approved_signals = []
        for signal in validated_signals:
            approval = self.risk_manager.approve(signal)
            if approval.approved:
                approved_signals.append(signal)
            else:
                logger.info(f"🚫 Risk rejected {signal.symbol}: {approval.reason}")

        # ── Step 7: Execute approved trades ────────────────────────
        for signal in approved_signals:
            self._execute_trade(signal)

        # ── Step 8: Manage existing positions ──────────────────────
        self._manage_positions()

    def _execute_trade(self, signal):
        """Place the actual order for a validated, approved signal."""
        try:
            order = self.client.place_order(signal)
            self.daily_trades += 1
            logger.info(
                f"📈 ORDER PLACED: {signal.direction} {signal.quantity}x {signal.symbol} "
                f"@ ~${signal.entry_price:.2f} | "
                f"SL: ${signal.stop_loss:.2f} | TP: ${signal.take_profit:.2f} | "
                f"Mode: {'PAPER' if self.settings.is_paper else 'LIVE'}"
            )
            return order
        except Exception as e:
            logger.error(f"Order failed for {signal.symbol}: {e}")

    def _manage_positions(self):
        """
        Check all open positions.
        Close any that have hit stop-loss or take-profit levels.
        """
        for position in self.portfolio.positions:
            current_price = self.client.get_quote(position.symbol)
            pnl_pct = (current_price - position.entry_price) / position.entry_price

            if position.direction == "SELL":
                pnl_pct = -pnl_pct  # Invert for short positions

            # Stop loss hit
            if pnl_pct <= -self.settings.risk.stop_loss_pct:
                logger.warning(
                    f"🔴 STOP LOSS: Closing {position.symbol} at ${current_price:.2f} "
                    f"(loss: {pnl_pct:.1%})"
                )
                self.client.close_position(position)
                self.daily_losses += 1

            # Take profit hit
            elif pnl_pct >= self.settings.risk.take_profit_pct:
                logger.info(
                    f"🟢 TAKE PROFIT: Closing {position.symbol} at ${current_price:.2f} "
                    f"(gain: {pnl_pct:.1%})"
                )
                self.client.close_position(position)
                self.daily_wins += 1

    def _daily_reset(self):
        """Reset daily counters at market open."""
        logger.info(f"Daily reset. Yesterday: {self.daily_wins}W / {self.daily_losses}L")
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0

    def _end_of_day_review(self):
        """
        15 minutes before close: review and optionally close all positions
        to avoid overnight risk (configurable).
        """
        logger.info("End-of-day review (15 min before close)...")
        # For now just log — can add "close all" logic here
        total = self.daily_wins + self.daily_losses
        if total > 0:
            win_rate = self.daily_wins / total
            logger.info(f"Today's win rate: {win_rate:.0%} ({self.daily_wins}W/{self.daily_losses}L)")

    def shutdown(self):
        """Gracefully stop the bot."""
        self.running = False
        logger.info("AlphaBot shutting down. Goodbye.")
