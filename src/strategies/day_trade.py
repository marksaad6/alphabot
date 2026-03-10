"""
src/strategies/day_trade.py
----------------------------
Day Trading Strategy: Intraday momentum scalping.

IMPORTANT - PDT RULE:
    Accounts under $25,000 are limited to 3 day trades per rolling
    5-business-day window. This strategy is automatically disabled
    if account value is below $25,000 unless you override in settings.yaml:
        day_trade_ignore_pdt_warning: true

How it works:
    - Uses 5-minute candles (intraday data)
    - Looks for stocks gapping up or down at open with volume surge
    - Enters on the first pullback after the initial move
    - Tight stop loss (0.5%) for quick exit if wrong
    - Target 1-1.5% gain, close position same day

Best market conditions: High volatility days, earnings reactions,
news-driven moves, gap-and-go setups.

Settings (config/settings.yaml):
    day_trade_enabled: true
    day_trade_min_gap_pct: 0.02       # Minimum 2% gap at open
    day_trade_min_volume_surge: 2.0   # Volume must be 2x average
    day_trade_stop_loss_pct: 0.005    # 0.5% stop (tight)
    day_trade_take_profit_pct: 0.012  # 1.2% target
    day_trade_max_per_day: 3          # Respect PDT limit
"""

import logging
import pandas as pd
from typing import Optional
from src.strategies.momentum import TradeSignal

logger = logging.getLogger(__name__)


class DayTradeStrategy:
    """
    Intraday gap-and-go scalping strategy.
    Enters on first pullback after a strong opening move.
    All positions closed before market close (3:45 PM ET).
    """

    def __init__(self, config):
        self.config = config
        self.trades_today = 0
        self.max_per_day = getattr(config, 'day_trade_max_per_day', 3)
        self.min_gap_pct = getattr(config, 'day_trade_min_gap_pct', 0.02)
        self.volume_surge = getattr(config, 'day_trade_min_volume_surge', 2.0)
        self.stop_loss_pct = getattr(config, 'day_trade_stop_loss_pct', 0.005)
        self.take_profit_pct = getattr(config, 'day_trade_take_profit_pct', 0.012)
        logger.info("DayTradeStrategy initialized (Gap-and-Go scalping)")

    def scan(self, symbols: list[str], client) -> list[TradeSignal]:
        """Scan for intraday gap-and-go setups."""
        if self.trades_today >= self.max_per_day:
            logger.debug(f"Day trade limit reached ({self.max_per_day}/day)")
            return []

        signals = []
        for symbol in symbols:
            try:
                signal = self._analyze(symbol, client)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"DayTrade error on {symbol}: {e}")
        return signals

    def _analyze(self, symbol: str, client) -> Optional[TradeSignal]:
        # Fetch today's 5-minute candles
        candles = client.get_price_history(
            symbol=symbol,
            period_type="day",
            period=1,
            frequency_type="minute",
            frequency=5,
        )

        if len(candles) < 10:
            return None

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.sort_values("datetime").reset_index(drop=True)

        # Get yesterday's close (last candle of previous session)
        daily = client.get_price_history(
            symbol=symbol,
            period_type="day",
            period=2,
            frequency_type="daily",
            frequency=1,
        )
        if len(daily) < 2:
            return None

        prev_close = daily[-2]["close"]
        open_price = df.iloc[0]["open"]
        current_price = df.iloc[-1]["close"]

        # Calculate gap
        gap_pct = (open_price - prev_close) / prev_close

        # Average volume (use first 30 min vs historical)
        early_volume = df.iloc[:6]["volume"].sum()   # First 30 min
        avg_30min_volume = df["volume"].mean() * 6   # Estimated normal 30-min vol

        volume_ratio = early_volume / avg_30min_volume if avg_30min_volume > 0 else 0

        # ── Gap-up setup ────────────────────────────────────────
        if gap_pct >= self.min_gap_pct and volume_ratio >= self.volume_surge:
            # Wait for first pullback (current price pulled back from high)
            session_high = df["high"].max()
            pullback_pct = (session_high - current_price) / session_high

            if 0.002 <= pullback_pct <= 0.008:  # 0.2% to 0.8% pullback
                stop_loss   = current_price * (1 - self.stop_loss_pct)
                take_profit = current_price * (1 + self.take_profit_pct)

                reasoning = (
                    f"Gap-up day trade: gapped +{gap_pct:.1%} from ${prev_close:.2f}. "
                    f"Volume surge: {volume_ratio:.1f}x average. "
                    f"Pulled back {pullback_pct:.1%} from high ${session_high:.2f}. "
                    f"Entry on pullback continuation."
                )

                logger.info(f"[SIGNAL] Day trade signal: BUY {symbol} @ ${current_price:.2f} "
                           f"(gap +{gap_pct:.1%}, vol {volume_ratio:.1f}x)")

                return TradeSignal(
                    symbol=symbol,
                    direction="BUY",
                    asset_type="EQUITY",
                    strategy_name="DayTrade",
                    entry_price=current_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    quantity=1,
                    reasoning=reasoning,
                )

        # ── Gap-down short setup (only if short selling enabled) ─
        elif gap_pct <= -self.min_gap_pct and volume_ratio >= self.volume_surge:
            session_low = df["low"].min()
            bounce_pct = (current_price - session_low) / session_low

            if 0.002 <= bounce_pct <= 0.008:
                stop_loss   = current_price * (1 + self.stop_loss_pct)
                take_profit = current_price * (1 - self.take_profit_pct)

                reasoning = (
                    f"Gap-down day trade: gapped {gap_pct:.1%} from ${prev_close:.2f}. "
                    f"Volume surge: {volume_ratio:.1f}x average. "
                    f"Dead-cat bounce of {bounce_pct:.1%} from low. "
                    f"Short continuation setup."
                )

                logger.info(f"[SIGNAL] Day trade signal: SELL {symbol} @ ${current_price:.2f} "
                           f"(gap {gap_pct:.1%}, vol {volume_ratio:.1f}x)")

                return TradeSignal(
                    symbol=symbol,
                    direction="SELL",
                    asset_type="EQUITY",
                    strategy_name="DayTrade",
                    entry_price=current_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    quantity=1,
                    reasoning=reasoning,
                )

        return None

    def reset_daily_count(self):
        """Call at market open each day to reset the PDT counter."""
        self.trades_today = 0