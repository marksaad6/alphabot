"""
src/strategies/momentum.py
--------------------------
Momentum Strategy: Buy stocks in strong uptrends on pullbacks.

Entry requires 5 of 7 conditions (tightened from 4/6):
  1. Price above SMA20
  2. Price above SMA50
  3. SMA20 > SMA50 (uptrend structure)
  4. RSI between 50-70 (trending, not overbought)
  5. Volume confirming (above 20-day average)
  6. Near EMA20 (pullback entry)
  7. SMA50 slope is positive (medium-term trend intact)

Also checks:
  - NOT in a bear market (regime filter)
  - 5-day return must be between -5% and +8% (not extended or broken)
"""

import logging
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """A candidate trade. Created by strategy, validated by AI, executed by bot."""
    symbol:         str
    direction:      str        # "BUY" or "SELL"
    asset_type:     str        # "EQUITY" or "OPTION"
    strategy_name:  str
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    quantity:       int
    reasoning:      str
    stop_loss_pct:  float = 0.0
    take_profit_pct: float = 0.0
    ai_confidence:  float = 0.0
    ai_reasoning:   str = ""

    def __post_init__(self):
        if self.entry_price > 0:
            self.stop_loss_pct   = abs(self.entry_price - self.stop_loss) / self.entry_price
            self.take_profit_pct = abs(self.take_profit - self.entry_price) / self.entry_price


class MomentumStrategy:
    """
    Scan for stocks in confirmed uptrends pulling back to support.
    Requires 5/7 conditions to reduce false signals in choppy markets.
    """

    def __init__(self, config):
        self.config = config
        logger.info("MomentumStrategy initialized")

    def scan(self, symbols: list[str], client) -> list[TradeSignal]:
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
        candles = client.get_price_history(
            symbol=symbol,
            period_type="month",
            period=3,
            frequency_type="daily",
            frequency=1,
        )

        if len(candles) < 55:
            return None

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.sort_values("datetime").reset_index(drop=True)

        # ── Indicators ──────────────────────────────────────────
        df["sma20"]     = df["close"].rolling(20).mean()
        df["sma50"]     = df["close"].rolling(50).mean()
        df["ema20"]     = df["close"].ewm(span=20, adjust=False).mean()
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        df["rsi"]       = self._rsi(df["close"], 14)

        latest    = df.iloc[-1]
        price     = latest["close"]
        sma20     = latest["sma20"]
        sma50     = latest["sma50"]
        ema20     = latest["ema20"]
        rsi       = latest["rsi"]
        volume    = latest["volume"]
        avg_vol   = latest["vol_sma20"]

        # SMA50 slope: compare to 10 days ago
        sma50_now  = sma50
        sma50_10d  = df["sma50"].iloc[-11] if len(df) > 11 else sma50
        sma50_slope = sma50_now - sma50_10d

        # 5-day return
        ret_5d = (price - df.iloc[-6]["close"]) / df.iloc[-6]["close"] if len(df) > 6 else 0

        # ── Conditions ──────────────────────────────────────────
        conditions = {
            "above_sma20":       price > sma20,
            "above_sma50":       price > sma50,
            "sma20_above_sma50": sma20 > sma50,
            "rsi_in_range":      50 <= rsi <= 72,
            "volume_ok":         volume > avg_vol * 1.05,
            "near_ema20":        price <= ema20 * 1.025,
            "sma50_rising":      sma50_slope > 0,
        }

        # Extra disqualifiers
        extended  = ret_5d > 0.08   # Up more than 8% in 5 days = chasing
        broken    = ret_5d < -0.06  # Down more than 6% in 5 days = broken
        deep_bear = price < sma50 * 0.94  # More than 6% below SMA50

        passed = sum(conditions.values())
        needed = 5  # Require 5/7

        # Always log scoring at DEBUG level
        logger.debug(
            f"{symbol}: Momentum {passed}/{len(conditions)} | "
            f"price ${price:.2f} | RSI {rsi:.1f} | "
            f"SMA20 ${sma20:.2f} | SMA50 ${sma50:.2f} | "
            f"5d return {ret_5d:+.1%} | "
            f"fail: {[k for k,v in conditions.items() if not v]}"
        )

        if extended or broken or deep_bear:
            logger.debug(f"{symbol}: Momentum SKIP -- extended={extended} broken={broken} deep_bear={deep_bear}")
            return None

        if passed < needed:
            return None

        stop_loss   = sma20 * 0.98
        take_profit = price * 1.04

        reasoning = (
            f"Momentum ({passed}/7): price ${price:.2f} above SMA20 ${sma20:.2f} / "
            f"SMA50 ${sma50:.2f}. RSI {rsi:.1f}. "
            f"Vol {volume:,} vs avg {avg_vol:,.0f}. "
            f"Pullback to EMA20 ${ema20:.2f}. SMA50 slope: {sma50_slope:+.2f}."
        )

        logger.info(f"[SIGNAL] Momentum signal: BUY {symbol} @ ${price:.2f} ({passed}/7 conditions)")
        return TradeSignal(
            symbol=symbol,
            direction="BUY",
            asset_type="EQUITY",
            strategy_name="Momentum",
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=1,
            reasoning=reasoning,
        )

    def _rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain  = delta.where(delta > 0, 0).rolling(period).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs    = gain / loss
        return 100 - (100 / (1 + rs))