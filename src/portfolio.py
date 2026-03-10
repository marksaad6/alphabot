"""
src/portfolio.py
----------------
Tracks current account state: cash, positions, total value.
Refreshes from Schwab API before each trading cycle.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    direction: str       # "BUY" (long) or "SELL" (short)
    quantity: int
    entry_price: float
    current_price: float = 0.0
    asset_type: str = "EQUITY"

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        pnl = (self.current_price - self.entry_price) * self.quantity
        return pnl if self.direction == "BUY" else -pnl

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0
        return self.unrealized_pnl / (self.entry_price * self.quantity)


class Portfolio:
    """Live view of the account's positions and cash."""

    def __init__(self, client):
        self.client = client
        self._account_hash: Optional[str] = None
        self.cash: float = 0.0
        self.total_value: float = 0.0
        self.positions: list[Position] = []

    @property
    def account_hash(self) -> str:
        if not self._account_hash:
            self._account_hash = self.client.get_account_number()
        return self._account_hash

    def refresh(self):
        """Sync portfolio state. In paper mode, uses simulated balance."""
        try:
            if hasattr(self.client, 'paper') and self.client.paper:
                # Paper mode — use simulated portfolio
                if self.total_value == 0:
                    self.cash = 5000.00       # Start with $5,000 simulated cash
                    self.total_value = 5000.00
                else:
                    # Recalculate from open positions
                    positions_value = sum(p.market_value for p in self.positions)
                    self.total_value = self.cash + positions_value
                return

            # Live mode — fetch from Schwab
            balances = self.client.get_account_balance(self.account_hash)
            self.cash = balances["cash"]
            self.total_value = balances["total_value"]

            raw_positions = self.client.get_account_positions(self.account_hash)
            self.positions = []
            for pos in raw_positions:
                instrument = pos.get("instrument", {})
                symbol = instrument.get("symbol", "")
                qty = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0)
                direction = "BUY" if pos.get("longQuantity", 0) > 0 else "SELL"
                avg_cost = pos.get("averagePrice", 0)
                market_val = pos.get("marketValue", 0)
                current_price = market_val / qty if qty > 0 else 0
                self.positions.append(Position(
                    symbol=symbol,
                    direction=direction,
                    quantity=qty,
                    entry_price=avg_cost,
                    current_price=current_price,
                ))

            logger.debug(f"Portfolio refreshed: ${self.cash:.2f} cash | "
                        f"${self.total_value:.2f} total | "
                        f"{len(self.positions)} positions")

        except Exception as e:
            logger.error(f"Portfolio refresh failed: {e}")
