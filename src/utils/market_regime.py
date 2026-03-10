"""
src/utils/market_regime.py
---------------------------
Detects the current market regime (bull / neutral / bear) using SPY.
The regime determines which strategies are active each session.

Regime logic (checks SPY daily):
  BULL   -- SPY above SMA20 and SMA50, both slopes up
             --> Run all strategies: momentum, mean reversion, swing, CSPs
  NEUTRAL -- SPY between SMA20 and SMA50 (choppy)
             --> Only mean reversion + swing trades + CSPs
  BEAR   -- SPY below SMA50, or down >5% in 10 days
             --> Only mean reversion (deep dips) + CSPs
             --> Momentum and day trade DISABLED automatically

This prevents the bot from fighting the trend.
"""

import logging
import pandas as pd
from enum import Enum

logger = logging.getLogger(__name__)


class Regime(Enum):
    BULL    = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR    = "BEAR"


class MarketRegimeDetector:

    def __init__(self):
        self._regime = Regime.NEUTRAL
        self._last_spy_price = 0.0
        self._last_checked_date = None

    @property
    def regime(self) -> Regime:
        return self._regime

    def is_bull(self) -> bool:
        return self._regime == Regime.BULL

    def is_bear(self) -> bool:
        return self._regime == Regime.BEAR

    def should_run_momentum(self) -> bool:
        """Only run momentum in bull or neutral markets."""
        return self._regime in (Regime.BULL, Regime.NEUTRAL)

    def should_run_mean_reversion(self) -> bool:
        """Mean reversion works in all regimes."""
        return True

    def should_run_swing(self) -> bool:
        """Swing trades OK in bull/neutral."""
        return self._regime in (Regime.BULL, Regime.NEUTRAL)

    def should_run_long_calls(self) -> bool:
        """Only buy calls in confirmed bull market."""
        return self._regime == Regime.BULL

    def update(self, client) -> Regime:
        """
        Fetch SPY data and detect regime.
        Call once per session at startup.
        """
        try:
            candles = client.get_price_history(
                symbol="SPY",
                period_type="month",
                period=3,
                frequency_type="daily",
                frequency=1,
            )

            if len(candles) < 55:
                logger.warning("Not enough SPY data for regime detection")
                return self._regime

            df = pd.DataFrame(candles)
            df = df.sort_values("datetime").reset_index(drop=True)
            df["sma20"] = df["close"].rolling(20).mean()
            df["sma50"] = df["close"].rolling(50).mean()

            latest = df.iloc[-1]
            price  = latest["close"]
            sma20  = latest["sma20"]
            sma50  = latest["sma50"]

            # SMA slopes (compare to 5 days ago)
            sma20_slope = sma20 - df["sma20"].iloc[-6]
            sma50_slope = sma50 - df["sma50"].iloc[-6]

            # 10-day return
            ret_10d = (price - df.iloc[-11]["close"]) / df.iloc[-11]["close"]

            # ── Regime determination ────────────────────────────
            if price < sma50 or ret_10d < -0.05:
                regime = Regime.BEAR
            elif price > sma20 and sma20_slope > 0 and sma50_slope > 0:
                regime = Regime.BULL
            else:
                regime = Regime.NEUTRAL

            old = self._regime
            self._regime = regime
            self._last_spy_price = price

            change = " (CHANGED)" if old != regime else ""
            logger.info(
                f"[REGIME] Market: {regime.value}{change} | "
                f"SPY ${price:.2f} | SMA20 ${sma20:.2f} | SMA50 ${sma50:.2f} | "
                f"10d return: {ret_10d:+.1%}"
            )

            if regime == Regime.BEAR:
                logger.warning(
                    "[REGIME] BEAR market detected -- "
                    "momentum and long calls DISABLED. "
                    "Running mean reversion + CSPs only."
                )

            return regime

        except Exception as e:
            logger.warning(f"Regime detection failed: {e} -- using {self._regime.value}")
            return self._regime