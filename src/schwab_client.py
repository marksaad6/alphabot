"""
src/schwab_client.py
--------------------
Wrapper around the schwabdev library.
Handles all communication with Charles Schwab's API:
  - Authentication & token refresh
  - Fetching quotes and historical prices
  - Placing and cancelling orders
  - Fetching account/position data

Install: pip install schwabdev
Docs: https://github.com/tylerebowers/Schwabdev
"""

import logging
from dataclasses import dataclass
from typing import Optional
import schwabdev

from config.settings import SchwabConfig

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    symbol: str
    last_price: float
    bid: float
    ask: float
    volume: int
    change_pct: float


@dataclass
class Order:
    order_id: str
    symbol: str
    status: str
    filled_price: Optional[float] = None


class SchwabClient:
    """
    Wrapper around schwabdev.Client.

    Paper mode: uses Schwab's built-in paperMoney environment.
    Live mode: uses real account.

    In both cases the API calls are identical --
    paper mode is toggled via the Schwab dashboard on your account.
    """

    def __init__(self, config: SchwabConfig, paper: bool = True):
        self.config = config
        self.paper = paper

        if paper:
            logger.info("[PAPER] Schwab client initialized in PAPER TRADING mode")
        else:
            logger.warning("[LIVE] Schwab client initialized in LIVE TRADING mode")

        # Initialize schwabdev client
        # schwabdev positional args: app_key, app_secret, tokens_file
        self._client = schwabdev.Client(
            config.app_key,
            config.app_secret,
            config.callback_url,
            config.token_path,
        )

    def get_account_number(self) -> str:
        """
        Fetches account hash from Schwab.
        Schwab uses hashed account numbers in API calls for security.
        """
        resp = self._client.linked_accounts().json()
        return resp[0]["hashValue"]

    def get_quote(self, symbol: str) -> float:
        """Get current price for a symbol."""
        resp = self._client.quote(symbol).json()
        return resp[symbol]["quote"]["lastPrice"]

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols in one API call."""
        resp = self._client.quotes(symbols).json()
        quotes = {}
        for sym, data in resp.items():
            q = data.get("quote", {})
            quotes[sym] = Quote(
                symbol=sym,
                last_price=q.get("lastPrice", 0),
                bid=q.get("bidPrice", 0),
                ask=q.get("askPrice", 0),
                volume=q.get("totalVolume", 0),
                change_pct=q.get("netPercentChangeInDouble", 0),
            )
        return quotes

    def get_price_history(
        self,
        symbol: str,
        period_type: str = "month",
        period: int = 3,
        frequency_type: str = "daily",
        frequency: int = 1,
    ) -> list[dict]:
        """
        Fetch OHLCV candle data for a symbol.

        Default: 3 months of daily candles.
        Returns list of {'open', 'high', 'low', 'close', 'volume', 'datetime'} dicts.
        """
        resp = self._client.price_history(
            symbol=symbol,
            periodType=period_type,
            period=period,
            frequencyType=frequency_type,
            frequency=frequency,
        ).json()

        candles = resp.get("candles", [])
        logger.debug(f"Fetched {len(candles)} candles for {symbol}")
        return candles

    def get_account_positions(self, account_hash: str) -> list[dict]:
        """Get all current open positions in the account."""
        resp = self._client.account_details(account_hash, fields="positions").json()
        return resp.get("securitiesAccount", {}).get("positions", [])

    def get_account_balance(self, account_hash: str) -> dict:
        """Get available cash and total account value."""
        resp = self._client.account_details(account_hash).json()
        balances = resp.get("securitiesAccount", {}).get("currentBalances", {})
        return {
            "cash": balances.get("cashBalance", 0),
            "total_value": balances.get("liquidationValue", 0),
        }

    def place_market_order(
        self,
        account_hash: str,
        symbol: str,
        quantity: int,
        instruction: str,  # "BUY" or "SELL"
    ) -> Order:
        """
        Place a simple market order for stocks.

        Note: Market orders fill immediately at current price.
        For production, consider limit orders to control slippage.
        """
        order_body = {
            "orderType": "MARKET",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": instruction,
                    "quantity": quantity,
                    "instrument": {
                        "symbol": symbol,
                        "assetType": "EQUITY",
                    },
                }
            ],
        }

        resp = self._client.place_order(account_hash, order_body)
        order_id = resp.headers.get("location", "").split("/")[-1]

        logger.info(f"Market order placed: {instruction} {quantity}x {symbol} | ID: {order_id}")
        return Order(order_id=order_id, symbol=symbol, status="PENDING")

    def place_limit_order(
        self,
        account_hash: str,
        symbol: str,
        quantity: int,
        instruction: str,
        limit_price: float,
    ) -> Order:
        """Place a limit order at a specific price."""
        order_body = {
            "orderType": "LIMIT",
            "session": "NORMAL",
            "duration": "DAY",
            "price": str(round(limit_price, 2)),
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": instruction,
                    "quantity": quantity,
                    "instrument": {
                        "symbol": symbol,
                        "assetType": "EQUITY",
                    },
                }
            ],
        }

        resp = self._client.place_order(account_hash, order_body)
        order_id = resp.headers.get("location", "").split("/")[-1]

        logger.info(
            f"Limit order placed: {instruction} {quantity}x {symbol} "
            f"@ ${limit_price:.2f} | ID: {order_id}"
        )
        return Order(order_id=order_id, symbol=symbol, status="PENDING")

    def cancel_order(self, account_hash: str, order_id: str):
        """Cancel an open order."""
        self._client.cancel_order(account_hash, order_id)
        logger.info(f"Order cancelled: {order_id}")

    def place_order(self, signal) -> Order:
        account_hash = self.get_account_number()

        if self.paper:
            # Paper mode — simulate the order locally, never send to Schwab
            import random
            fake_id = f"PAPER-{random.randint(10000,99999)}"
            logger.info(f"[PAPER TRADE] {signal.direction} {signal.quantity}x "
                    f"{signal.symbol} @ ${signal.entry_price:.2f} | ID: {fake_id}")
            return Order(order_id=fake_id, symbol=signal.symbol, 
                        status="FILLED", filled_price=signal.entry_price)

        # Live mode only — real order
        if signal.asset_type == "EQUITY":
            return self.place_limit_order(
                account_hash=account_hash,
                symbol=signal.symbol,
                quantity=signal.quantity,
                instruction=signal.direction,
                limit_price=signal.entry_price,
            )

    def close_position(self, position) -> Order:
        """Close an open position (sell if long, buy if short)."""
        account_hash = self.get_account_number()
        close_instruction = "SELL" if position.direction == "BUY" else "BUY"
        return self.place_market_order(
            account_hash=account_hash,
            symbol=position.symbol,
            quantity=position.quantity,
            instruction=close_instruction,
        )