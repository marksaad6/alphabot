"""
src/news_feed.py
----------------
Fetches real-time news and market context for Claude to use
when evaluating every trade signal.

Provides three layers of context:
  1. Symbol-specific headlines  (Yahoo Finance RSS, free, no key)
  2. Earnings date guard        (blocks trades within 3 days of earnings)
  3. Macro calendar warnings    (Fed days, CPI, jobs report)
  4. Market breadth snapshot    (VIX level, SPY trend, sector ETFs)

All results cached 15 minutes to avoid hammering free endpoints.
"""

import logging
import time
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

CACHE_TTL = 900  # 15 min

# Upcoming macro events -- update weekly
# Format: ("YYYY-MM-DD", "Event Name")
MACRO_CALENDAR = [
    ("2026-03-12", "CPI Inflation Report"),
    ("2026-03-18", "FOMC Meeting Begins"),
    ("2026-03-19", "Fed Rate Decision + Press Conference"),
    ("2026-03-28", "PCE Inflation Data"),
    ("2026-04-02", "Non-Farm Payrolls (Jobs Report)"),
    ("2026-04-10", "CPI Inflation Report"),
    ("2026-04-15", "Tax Day -- historically volatile"),
]

# Earnings dates for watchlist symbols -- update each quarter
# Format: ("SYMBOL", "YYYY-MM-DD")
EARNINGS_CALENDAR = [
    ("AAPL",  "2026-04-30"),
    ("MSFT",  "2026-04-29"),
    ("NVDA",  "2026-05-28"),
    ("AMZN",  "2026-04-30"),
    ("GOOGL", "2026-04-29"),
    ("META",  "2026-04-29"),
    ("TSLA",  "2026-04-22"),
    ("AMD",   "2026-04-28"),
    ("SPY",   ""),   # ETF -- no earnings
    ("QQQ",   ""),   # ETF -- no earnings
]


class NewsFeed:

    def __init__(self):
        self._cache: dict = {}

    def get_full_context(self, symbol: str, schwab_client=None) -> str:
        """
        Build the complete context block injected into Claude's prompt.
        Combines news, earnings warning, macro events, and market breadth.
        """
        cache_key = f"ctx_{symbol}"
        now = time.time()
        if cache_key in self._cache:
            ts, result = self._cache[cache_key]
            if now - ts < CACHE_TTL:
                return result

        sections = []

        # 1. Earnings guard
        earnings_warning = self._check_earnings(symbol)
        if earnings_warning:
            sections.append(earnings_warning)

        # 2. Macro events
        macro = self._get_macro_warnings()
        if macro:
            sections.append("UPCOMING MACRO EVENTS (high risk for open positions):")
            for m in macro:
                sections.append(f"  ! {m}")

        # 3. Symbol news
        headlines = self._fetch_yahoo_rss(symbol)
        if headlines:
            sections.append(f"RECENT NEWS ({symbol}, last 24h):")
            for h in headlines:
                sections.append(f"  - {h}")
        else:
            sections.append(f"RECENT NEWS ({symbol}): No major headlines found.")

        # 4. Market breadth (VIX + sector ETFs via Schwab quotes)
        breadth = self._get_market_breadth(schwab_client)
        if breadth:
            sections.append("MARKET CONDITIONS:")
            for line in breadth:
                sections.append(f"  {line}")

        result = "\n".join(sections)
        self._cache[cache_key] = (now, result)
        return result

    # ── Earnings Guard ────────────────────────────────────────
    def _check_earnings(self, symbol: str) -> str:
        """
        Returns a strong warning string if earnings are within 3 days.
        Returns empty string if safe.
        """
        today = datetime.now().date()
        for sym, date_str in EARNINGS_CALENDAR:
            if sym.upper() != symbol.upper():
                continue
            if not date_str:
                return ""
            try:
                earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_away = (earnings_date - today).days
                if 0 <= days_away <= 3:
                    return (
                        f"!! EARNINGS ALERT: {symbol} reports earnings in {days_away} day(s) "
                        f"({earnings_date}). HIGH GAP RISK. "
                        f"Strongly consider skipping this trade."
                    )
                elif -1 <= days_away < 0:
                    return (
                        f"!! EARNINGS JUST REPORTED: {symbol} reported earnings yesterday. "
                        f"Post-earnings volatility likely. Extra caution advised."
                    )
            except ValueError:
                pass
        return ""

    # ── Macro Calendar ────────────────────────────────────────
    def _get_macro_warnings(self) -> list:
        today = datetime.now().date()
        warnings = []
        for date_str, event in MACRO_CALENDAR:
            try:
                event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_away = (event_date - today).days
                if days_away == 0:
                    warnings.append(f"TODAY -- {event} (avoid new positions, close before event)")
                elif days_away == 1:
                    warnings.append(f"TOMORROW -- {event} (overnight gap risk)")
                elif days_away == 2:
                    warnings.append(f"IN 2 DAYS -- {event} (be cautious with swing entries)")
            except ValueError:
                pass
        return warnings

    # ── Yahoo Finance RSS ─────────────────────────────────────
    def _fetch_yahoo_rss(self, symbol: str) -> list:
        url = (
            f"https://feeds.finance.yahoo.com/rss/2.0/headline"
            f"?s={symbol}&region=US&lang=en-US"
        )
        headlines = []
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 AlphaBot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            root = ET.fromstring(raw)
            cutoff = datetime.utcnow() - timedelta(hours=24)

            for item in root.iter("item"):
                title = item.findtext("title", "").strip()
                pub_str = item.findtext("pubDate", "").strip()
                try:
                    pub_dt = datetime.strptime(pub_str, "%a, %d %b %Y %H:%M:%S %z")
                    pub_dt = pub_dt.replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass
                if title and len(title) > 10:
                    headlines.append(title)
                if len(headlines) >= 5:
                    break

        except Exception as e:
            logger.debug(f"News fetch failed for {symbol}: {e}")

        return headlines

    # ── Market Breadth ────────────────────────────────────────
    def _get_market_breadth(self, schwab_client) -> list:
        """
        Fetch VIX and key sector ETF prices to give Claude macro context.
        Returns list of readable strings.
        """
        if not schwab_client:
            return []

        breadth = []
        symbols_to_check = {
            "VIX":  "Fear gauge",
            "SPY":  "S&P 500",
            "QQQ":  "Nasdaq 100",
            "IWM":  "Small caps (Russell 2000)",
            "XLF":  "Financials sector",
            "XLK":  "Technology sector",
            "XLE":  "Energy sector",
        }

        cache_key = "breadth"
        now = time.time()
        if cache_key in self._cache:
            ts, result = self._cache[cache_key]
            if now - ts < CACHE_TTL:
                return result

        for sym, label in symbols_to_check.items():
            try:
                price = schwab_client.get_quote(sym)
                if price and price > 0:
                    if sym == "VIX":
                        level = "LOW (calm)" if price < 15 else "ELEVATED (nervous)" if price < 25 else "HIGH (fearful)"
                        breadth.append(f"VIX: {price:.1f} -- {level}")
                    else:
                        breadth.append(f"{sym} ({label}): ${price:.2f}")
            except Exception:
                pass

        self._cache[cache_key] = (now, breadth)
        return breadth

    def update_earnings(self, symbol: str, date_str: str):
        """Manually update an earnings date at runtime."""
        for i, (sym, _) in enumerate(EARNINGS_CALENDAR):
            if sym.upper() == symbol.upper():
                EARNINGS_CALENDAR[i] = (symbol.upper(), date_str)
                logger.info(f"Updated earnings date: {symbol} -> {date_str}")
                return
        EARNINGS_CALENDAR.append((symbol.upper(), date_str))
        logger.info(f"Added earnings date: {symbol} -> {date_str}")