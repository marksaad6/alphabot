"""
src/ai/credit_monitor.py
-------------------------
Monitors Anthropic API credit balance and warns before the bot
gets stuck mid-session with no AI filter available.

Two safety behaviors:
  1. LOW CREDIT WARNING  -- logs a warning when balance drops below threshold
  2. FALLBACK MODE       -- if credits run out mid-session, switches to a
                           rule-based fallback filter instead of crashing.
                           Trades can still execute, just without AI validation.

We check once at startup and then every 30 minutes during the session.
"""

import logging
import time
from dataclasses import dataclass
import anthropic

logger = logging.getLogger(__name__)

LOW_CREDIT_THRESHOLD   = 2.00   # Warn when below $2.00
EMPTY_CREDIT_THRESHOLD = 0.10   # Switch to fallback below $0.10


@dataclass
class CreditStatus:
    balance: float
    is_low: bool
    is_empty: bool
    fallback_active: bool
    last_checked: float


class CreditMonitor:
    """
    Tracks Anthropic API credit balance.
    Switches AI analyzer to rule-based fallback if credits run out
    so the bot never crashes or freezes mid-session.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = anthropic.Anthropic(api_key=api_key)
        self._status = CreditStatus(
            balance=999.0,
            is_low=False,
            is_empty=False,
            fallback_active=False,
            last_checked=0,
        )
        self._check_interval = 1800  # Re-check every 30 minutes
        logger.info("CreditMonitor initialized (checks every 30 min)")

    @property
    def fallback_active(self) -> bool:
        return self._status.fallback_active

    def check(self, force: bool = False) -> CreditStatus:
        """
        Check current credit balance.
        Skips the API call if checked recently, unless force=True.
        """
        now = time.time()
        if not force and (now - self._status.last_checked) < self._check_interval:
            return self._status

        balance = self._probe_with_test_call()

        self._status = CreditStatus(
            balance=balance,
            is_low=balance < LOW_CREDIT_THRESHOLD,
            is_empty=balance < EMPTY_CREDIT_THRESHOLD,
            fallback_active=balance < EMPTY_CREDIT_THRESHOLD,
            last_checked=time.time(),
        )

        self._log_status()
        return self._status

    def _probe_with_test_call(self) -> float:
        """
        Send a minimal 1-token request to verify credits exist.
        Uses claude-haiku (cheapest) to minimise cost of the probe itself.
        Returns 999.0 if working, 0.0 if out of credits.
        """
        try:
            self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            logger.debug("Credit probe: API responding normally")
            return 999.0

        except anthropic.APIStatusError as e:
            error_str = str(e).lower()
            if "credit balance is too low" in error_str or "insufficient" in error_str:
                return 0.0
            # Other API error -- don't assume out of credits
            logger.warning(f"Credit probe inconclusive: {e}")
            return 999.0

        except Exception as e:
            logger.warning(f"Credit probe failed (assuming OK): {e}")
            return 999.0

    def _log_status(self):
        s = self._status
        if s.is_empty:
            logger.error(
                "[CREDIT EMPTY] Anthropic credits exhausted -- "
                "AI filter DISABLED. Bot running on rule-based fallback. "
                "Add credits at: console.anthropic.com/settings/billing"
            )
        elif s.is_low:
            logger.warning(
                f"[CREDIT LOW] Balance below ${LOW_CREDIT_THRESHOLD:.2f} -- "
                "Add credits soon at console.anthropic.com/settings/billing"
            )
        else:
            logger.info("[CREDIT OK] Anthropic API credits available")

    def get_status_summary(self) -> str:
        s = self._status
        if s.fallback_active:
            return "[CREDIT EMPTY] AI fallback active"
        elif s.is_low:
            return f"[CREDIT LOW] Add credits soon"
        return "[CREDIT OK]"