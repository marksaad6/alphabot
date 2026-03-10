"""
src/risk_manager.py
--------------------
Risk Manager: The gatekeeper before any trade is executed.

Every signal must pass ALL risk checks before being approved.
This is what keeps a bad day from becoming a catastrophic loss.

Rules enforced:
  1. Max position size (% of portfolio per trade)
  2. Max total exposure (% of portfolio in the market)
  3. Max daily trades (prevent overtrading)
  4. Minimum cash reserve (always keep some cash)
  5. No duplicate positions (don't double-up on same symbol)
  6. PDT rule awareness (Pattern Day Trader warning under $25K)
"""

import logging
from dataclasses import dataclass

from config.settings import RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class RiskApproval:
    approved: bool
    reason: str
    adjusted_quantity: int = 0  # Risk manager may reduce size


class RiskManager:

    def __init__(self, config: RiskConfig, portfolio):
        self.config = config
        self.portfolio = portfolio
        logger.info("RiskManager initialized")

    def approve(self, signal) -> RiskApproval:
        """
        Run all risk checks on a signal.
        Returns approval decision and the approved position size.
        """
        portfolio_value = self.portfolio.total_value
        cash = self.portfolio.cash

        # ── Rule 1: Min cash reserve ────────────────────────────────
        if cash < self.config.min_cash_reserve:
            return RiskApproval(
                approved=False,
                reason=f"Cash ${cash:.0f} below minimum ${self.config.min_cash_reserve:.0f}"
            )

        # ── Rule 2: Max total exposure ──────────────────────────────
        current_exposure = portfolio_value - cash
        max_exposure = portfolio_value * self.config.max_total_exposure_pct
        if current_exposure >= max_exposure:
            return RiskApproval(
                approved=False,
                reason=f"Total exposure {current_exposure/portfolio_value:.0%} at max "
                       f"{self.config.max_total_exposure_pct:.0%}"
            )

        # ── Rule 3: No duplicate positions ─────────────────────────
        existing_symbols = [p.symbol for p in self.portfolio.positions]
        if signal.symbol in existing_symbols:
            return RiskApproval(
                approved=False,
                reason=f"Already holding {signal.symbol}"
            )

        # ── Rule 4: Calculate position size ────────────────────────
        max_risk_dollars = portfolio_value * self.config.max_position_size_pct
        # Risk per share = entry - stop_loss
        risk_per_share = abs(signal.entry_price - signal.stop_loss)

        if risk_per_share <= 0:
            return RiskApproval(
                approved=False,
                reason="Invalid stop loss (no risk per share)"
            )

        # Shares we can buy within our risk budget
        quantity = int(max_risk_dollars / risk_per_share)

        if quantity <= 0:
            return RiskApproval(
                approved=False,
                reason=f"Risk per share ${risk_per_share:.2f} too high for budget ${max_risk_dollars:.0f}"
            )

        # Cap by cash available
        max_shares_by_cash = int((cash - self.config.min_cash_reserve) / signal.entry_price)
        quantity = min(quantity, max_shares_by_cash)

        if quantity <= 0:
            return RiskApproval(
                approved=False,
                reason="Insufficient cash for even 1 share within risk limits"
            )

        # ── Rule 5: PDT warning (informational, not blocking) ───────
        if portfolio_value < 25_000:
            logger.warning(
                "⚠️  PDT Warning: Account under $25K. "
                "Limit to 3 day trades per rolling 5-day window. "
                "Consider swing trades (hold >1 day) to avoid PDT restriction."
            )

        # All checks passed
        signal.quantity = quantity  # Update signal with approved size

        trade_value = quantity * signal.entry_price
        logger.info(
            f"✅ Risk approved: {signal.symbol} | "
            f"Qty: {quantity} | Value: ${trade_value:.0f} | "
            f"Max loss: ${quantity * risk_per_share:.0f}"
        )

        return RiskApproval(
            approved=True,
            reason="All risk checks passed",
            adjusted_quantity=quantity,
        )
