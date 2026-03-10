"""
src/strategies/swing_trade.py
------------------------------
Swing Trading Strategy: Hold positions 2-10 days to capture
larger price moves. No PDT restrictions since positions are held
overnight.

Best for $2,000-$25,000 accounts because:
    - No PDT rule (not day trading)
    - Larger profit targets (5-15%) vs day trading (1-2%)
    - Less screen time needed — set and forget

How it works:
    - Weekly chart trend must be UP (price above 10-week SMA)
    - Daily chart pulls back to key support (SMA20 or Fibonacci level)
    - RSI resets to 40-55 range (healthy pullback, not broken)
    - Volume dries up on pullback (sellers exhausted)
    - Entry when daily candle shows reversal (hammer, engulfing)

Settings (config/settings.yaml):
    swing_trade_enabled: true
    swing_hold_days_min: 2           # Minimum hold before considering exit
    swing_hold_days_max: 10          # Force close after 10 days if not hit
    swing_stop_loss_pct: 0.04        # 4% stop (wider for swing)
    swing_take_profit_pct: 0.10      # 10% target (5-15% typical)
    swing_rsi_min: 38                # RSI floor for entry
    swing_rsi_max: 58                # RSI ceiling for entry
"""

import logging
import pandas as pd
import numpy as np
from typing import Optional
from src.strategies.momentum import TradeSignal

logger = logging.getLogger(__name__)


class SwingTradeStrategy:
    """
    Multi-day swing trading. Holds 2-10 days.
    Targets larger moves (5-15%) with wider stops (3-5%).
    No PDT restrictions.
    """

    def __init__(self, config):
        self.config = config
        self.stop_loss_pct   = getattr(config, 'swing_stop_loss_pct', 0.04)
        self.take_profit_pct = getattr(config, 'swing_take_profit_pct', 0.10)
        self.rsi_min = getattr(config, 'swing_rsi_min', 38)
        self.rsi_max = getattr(config, 'swing_rsi_max', 58)
        logger.info("SwingTradeStrategy initialized (2-10 day holds)")

    def scan(self, symbols: list[str], client) -> list[TradeSignal]:
        signals = []
        for symbol in symbols:
            try:
                signal = self._analyze(symbol, client)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"SwingTrade error on {symbol}: {e}")
        return signals

    def _analyze(self, symbol: str, client) -> Optional[TradeSignal]:
        # 6 months of daily data
        candles = client.get_price_history(
            symbol=symbol,
            period_type="month",
            period=6,
            frequency_type="daily",
            frequency=1,
        )

        if len(candles) < 60:
            return None

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.sort_values("datetime").reset_index(drop=True)

        # ── Indicators ──────────────────────────────────────────
        df["sma20"]  = df["close"].rolling(20).mean()
        df["sma50"]  = df["close"].rolling(50).mean()
        df["sma10w"] = df["close"].rolling(50).mean()  # ~10-week proxy
        df["rsi"]    = self._rsi(df["close"], 14)
        df["atr"]    = self._atr(df, 14)

        # Volume trend (declining volume on pullback = bullish)
        df["vol_sma10"] = df["volume"].rolling(10).mean()

        latest  = df.iloc[-1]
        prev    = df.iloc[-2]
        price   = latest["close"]
        rsi     = latest["rsi"]
        sma20   = latest["sma20"]
        sma50   = latest["sma50"]
        volume  = latest["volume"]
        avg_vol = latest["vol_sma10"]

        # ── Weekly trend filter ─────────────────────────────────
        # Must be in longer-term uptrend
        if price < sma50 * 0.95:  # More than 5% below SMA50 = no
            return None

        # ── Conditions ──────────────────────────────────────────
        conditions = {
            "weekly_uptrend":   price > df["close"].rolling(50).mean().iloc[-1],
            "rsi_pullback":     self.rsi_min <= rsi <= self.rsi_max,
            "near_support":     price <= sma20 * 1.03,   # Within 3% of SMA20
            "volume_drying":    volume < avg_vol * 0.85,  # Volume 15% below avg
            "sma20_slope_up":   sma20 > df["sma20"].iloc[-5],  # SMA20 trending up
        }

        passed = sum(conditions.values())

        if passed < 3:
            logger.debug(
                f"{symbol}: Swing failed ({passed}/5) -- "
                f"{[k for k,v in conditions.items() if not v]}"
            )
            return None

        # ── Check for reversal candle ────────────────────────────
        # Hammer: lower wick > 2x body, small upper wick
        body   = abs(latest["close"] - latest["open"])
        lo_wck = latest["open"] - latest["low"] if latest["close"] > latest["open"] else latest["close"] - latest["low"]
        is_hammer = lo_wck > body * 1.5 and body > 0

        # Bullish engulfing: today's body engulfs yesterday's
        is_engulfing = (
            latest["close"] > latest["open"] and
            prev["close"] < prev["open"] and
            latest["close"] > prev["open"] and
            latest["open"] < prev["close"]
        )

        if not (is_hammer or is_engulfing):
            logger.debug(f"{symbol}: Swing conditions met but no reversal candle")
            return None

        candle_type = "hammer" if is_hammer else "bullish engulfing"

        # ── Entry levels ─────────────────────────────────────────
        stop_loss   = sma20 * (1 - self.stop_loss_pct)   # Below SMA20 support
        take_profit = price * (1 + self.take_profit_pct)  # 10% target

        reasoning = (
            f"Swing trade setup ({candle_type}): "
            f"RSI {rsi:.0f} in pullback zone, "
            f"price ${price:.2f} near SMA20 ${sma20:.2f}. "
            f"Volume drying up ({volume:,} vs avg {avg_vol:,.0f}). "
            f"Hold target: 2-10 days."
        )

        logger.info(f"[SIGNAL] Swing trade: BUY {symbol} @ ${price:.2f} "
                   f"({candle_type}, RSI {rsi:.0f})")

        return TradeSignal(
            symbol=symbol,
            direction="BUY",
            asset_type="EQUITY",
            strategy_name="SwingTrade",
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

    def _atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl  = df["high"] - df["low"]
        hpc = (df["high"] - df["close"].shift()).abs()
        lpc = (df["low"]  - df["close"].shift()).abs()
        tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
        return tr.rolling(period).mean()