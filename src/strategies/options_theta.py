"""
src/strategies/options_theta.py
--------------------------------
Theta / Cash-Secured Puts Strategy: Generate income by selling options premium.

This is the most reliable income-generating options strategy for smaller accounts.

How it works:
  1. Pick a stock you'd be happy to OWN at a lower price
  2. Sell a PUT option below the current price (out-of-the-money)
  3. Collect premium (income) immediately
  4. Two outcomes:
     a. Stock stays above your strike → option expires worthless → you keep all premium [OK]
     b. Stock drops below strike → you buy the stock at a discount (you already wanted it) [OK]

Target: Sell puts with:
  - 30–45 DTE (days to expiration) -- sweet spot for theta decay
  - Delta ~0.20–0.30 (20–30% chance of being assigned = 70–80% win rate baseline)
  - On high-quality large-cap stocks with strong support levels

Capital requirement: ~$2,500–$5,000 per contract (depends on strike)
"""

import logging
from typing import Optional
from dataclasses import dataclass

from src.strategies.momentum import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class OptionsSignal(TradeSignal):
    """Extended TradeSignal for options trades."""
    option_symbol: str = ""
    expiration: str = ""
    strike: float = 0.0
    option_type: str = ""  # "PUT" or "CALL"
    delta: float = 0.0
    premium_collected: float = 0.0
    dte: int = 0  # Days to expiration


class ThetaOptionsStrategy:
    """
    Sell cash-secured puts on quality stocks.
    Target: ~70–80% win rate by selling 20-delta puts.
    """

    # Best symbols for cash-secured puts:
    # - High liquidity (tight spreads)
    # - Stable large-caps, not hyper-volatile meme stocks
    PREFERRED_SYMBOLS = [
        "SPY", "QQQ", "AAPL", "MSFT", "AMZN",
        "GOOGL", "NVDA", "AMD", "META"
    ]

    def __init__(self, config):
        self.config = config
        logger.info("ThetaOptionsStrategy initialized (Cash-Secured Puts)")

    def scan(self, symbols: list[str], client) -> list[TradeSignal]:
        """
        Scan for cash-secured put opportunities.

        Only runs on preferred symbols with good options liquidity.
        """
        signals = []

        # Only use symbols with good options liquidity
        eligible = [s for s in symbols if s in self.PREFERRED_SYMBOLS]

        for symbol in eligible:
            try:
                signal = self._find_put_to_sell(symbol, client)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"ThetaOptions error on {symbol}: {e}")

        return signals

    def _find_put_to_sell(self, symbol: str, client) -> Optional[OptionsSignal]:
        """
        Find the best cash-secured put to sell for a given symbol.

        Targets:
        - 30–45 DTE
        - Delta ~0.20–0.25 (OTM put)
        - Strike at or near a key support level
        """
        # Fetch options chain from Schwab
        try:
            chain = client._client.option_chains(
                symbol=symbol,
                contractType="PUT",
                strikeCount=10,
                strategy="SINGLE",
                daysToExpiration=37,
                optionType="S",
            ).json()
        except Exception as e:
            logger.debug(f"Could not fetch options chain for {symbol}: {e}")
            return None

        # Get current stock price
        underlying_price = chain.get("underlyingPrice", 0)
        if not underlying_price:
            return None

        # Find the right expiration (30–45 DTE)
        put_map = chain.get("putExpDateMap", {})
        best_option = None
        best_score = 0

        for exp_date, strikes in put_map.items():
            # Parse DTE from expiration string (format: "2024-12-20:30")
            dte = int(exp_date.split(":")[1]) if ":" in exp_date else 0
            if not (28 <= dte <= 47):
                continue

            for strike_str, options in strikes.items():
                strike = float(strike_str)
                option = options[0] if options else {}

                delta = abs(option.get("delta", 0))
                bid = option.get("bid", 0)
                ask = option.get("ask", 0)
                volume = option.get("totalVolume", 0)

                if bid <= 0 or delta == 0:
                    continue

                # Score: prefer delta ~0.20-0.25, decent premium, good volume
                if 0.15 <= delta <= 0.30:
                    mid_price = (bid + ask) / 2
                    # Annualized return on capital
                    annual_return = (mid_price / strike) * (365 / dte)
                    score = annual_return * volume

                    if score > best_score:
                        best_score = score
                        best_option = {
                            "option_symbol": option.get("symbol", ""),
                            "expiration": exp_date.split(":")[0],
                            "strike": strike,
                            "premium": mid_price,
                            "delta": delta,
                            "dte": dte,
                            "bid": bid,
                            "ask": ask,
                        }

        if not best_option:
            return None

        # A cash-secured put requires having cash = strike * 100
        capital_required = best_option["strike"] * 100
        if capital_required > 5000:  # Don't tie up more than $5K per trade
            logger.debug(f"{symbol}: Capital required ${capital_required:,.0f} too high")
            return None

        reasoning = (
            f"Selling {best_option['dte']}-DTE PUT at strike ${best_option['strike']:.0f} "
            f"(delta: {best_option['delta']:.2f}). "
            f"Premium: ${best_option['premium']:.2f}/contract = ${best_option['premium']*100:.0f} income. "
            f"Underlying price: ${underlying_price:.2f}. "
            f"Capital required: ${capital_required:,.0f}."
        )

        logger.info(
            f"[SIGNAL] Theta signal: SELL PUT {symbol} "
            f"${best_option['strike']:.0f} exp {best_option['expiration']} "
            f"@ ${best_option['premium']:.2f}"
        )

        return OptionsSignal(
            symbol=symbol,
            direction="SELL",
            asset_type="OPTION",
            strategy_name="ThetaCSP",
            entry_price=best_option["premium"],
            stop_loss=best_option["premium"] * 2.0,   # Close if option doubles in price (loss)
            take_profit=best_option["premium"] * 0.25, # Close at 75% profit (25% of premium remaining)
            quantity=1,  # 1 contract = 100 shares
            reasoning=reasoning,
            option_symbol=best_option["option_symbol"],
            expiration=best_option["expiration"],
            strike=best_option["strike"],
            option_type="PUT",
            delta=best_option["delta"],
            premium_collected=best_option["premium"] * 100,
            dte=best_option["dte"],
        )