"""
src/strategies/momentum.py
--------------------------
Momentum Strategy: Buy stocks in strong uptrends.

Logic:
  - Symbol must be above its 20-day and 50-day moving average
  - RSI between 50–70 (trending up but not overbought)
  - Volume must be above 20-day average (confirming the move)
  - Entry on pullback to the 20 EMA in an uptrend

This is one of the most proven strategies in quant trading.
Win rates of 55-65% are typical; the AI layer aims to push this higher.
"""

import logging
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """A candidate trade. Created by a strategy, validated by AI, executed by the bot."""
    symbol: str
    direction: str           # "BUY" or "SELL"
    asset_type: str          # "EQUITY" or "OPTION"
    strategy_name: str
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int
    reasoning: str

    # Computed fields
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0

    # Filled in after AI analysis
    ai_confidence: float = 0.0
    ai_reasoning: str = ""

    def __post_init__(self):
        if self.entry_price > 0:
            self.stop_loss_pct = abs(self.entry_price - self.stop_loss) / self.entry_price
            self.take_profit_pct = abs(self.take_profit - self.entry_price) / self.entry_price


class MomentumStrategy:
    """
    Scan for stocks in strong uptrends and generate buy signals
    when they pull back to key support levels.
    """

    def __init__(self, config):
        self.config = config
        logger.info("MomentumStrategy initialized")

    def scan(self, symbols: list[str], client) -> list[TradeSignal]:
        """
        Scan all symbols in the watchlist.
        Return a list of TradeSignal objects for qualifying setups.
        """
        signals = []

        for symbol in symbols:
            try:
                signal = self._analyze(symbol, client)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"MomentumStrategy error on {symbol}: {e}")

        return signals

    def _analyze(self, symbol: str, client) -> Optional[TradeSignal]:
        """Run momentum analysis on a single symbol."""
        # Fetch 3 months of daily data
        candles = client.get_price_history(
            symbol=symbol,
            period_type="month",
            period=3,
            frequency_type="daily",
            frequency=1,
        )

        if len(candles) < 55:
            logger.debug(f"{symbol}: Not enough data ({len(candles)} candles)")
            return None

        # Convert to DataFrame for easier analysis
        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.sort_values("datetime").reset_index(drop=True)

        # ── Calculate indicators ────────────────────────────────────
        df["sma20"] = df["close"].rolling(20).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        df["rsi"] = self._calculate_rsi(df["close"], period=14)

        # Get latest values
        latest = df.iloc[-1]
        price = latest["close"]
        sma20 = latest["sma20"]
        sma50 = latest["sma50"]
        ema20 = latest["ema20"]
        rsi = latest["rsi"]
        volume = latest["volume"]
        avg_volume = latest["vol_sma20"]

        # ── Check momentum conditions ───────────────────────────────
        conditions = {
            "above_sma20": price > sma20,
            "above_sma50": price > sma50,
            "sma20_above_sma50": sma20 > sma50,      # Uptrend structure
            "rsi_in_range": 50 <= rsi <= 70,          # Trending, not overbought
            "volume_confirming": volume > avg_volume * 1.1,  # 10% above avg
            "near_ema20": price <= ema20 * 1.02,      # Within 2% of EMA (pullback entry)
        }

        passed = sum(conditions.values())
        total = len(conditions)

        if passed < 4:  # Need at least 4/6 conditions
            logger.debug(f"{symbol}: Momentum failed ({passed}/{total}) -- {[k for k,v in conditions.items() if not v]}")
            return None

        # ── Signal confirmed -- calculate trade levels ───────────────
        stop_loss = sma20 * 0.98      # 2% below SMA20 (structure support)
        take_profit = price * 1.04    # 4% target (2:1 risk/reward)

        # Calculate position size (will be refined by risk manager)
        quantity = 1  # Placeholder -- risk manager calculates real size

        reasoning = (
            f"Momentum setup: price ${price:.2f} above SMA20 ${sma20:.2f} and "
            f"SMA50 ${sma50:.2f}. RSI: {rsi:.1f}. "
            f"Volume: {volume:,} vs avg {avg_volume:,.0f}. "
            f"Pulling back to EMA20 ${ema20:.2f} in uptrend."
        )

        logger.info(f"[SIGNAL] Momentum signal: BUY {symbol} @ ${price:.2f}")
        return TradeSignal(
            symbol=symbol,
            direction="BUY",
            asset_type="EQUITY",
            strategy_name="Momentum",
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            reasoning=reasoning,
        )

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate the Relative Strength Index (RSI)."""
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))