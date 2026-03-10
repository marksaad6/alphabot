"""
src/session_logger.py
---------------------
Tracks and displays a summary of every trading session.

Printed when the bot starts and when it shuts down (Ctrl+C).
Also saves a session log to logs/sessions.csv for tracking
performance over time.

Session summary shows:
  - Date and duration
  - Total signals found vs executed
  - Win / Loss / Open counts
  - Realized P&L for the session
  - Running all-time stats from sessions.csv
"""

import logging
import csv
import os
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SESSIONS_FILE = "logs/sessions.csv"
TRADES_FILE   = "logs/trades.csv"


@dataclass
class TradeRecord:
    timestamp:  str
    symbol:     str
    direction:  str
    strategy:   str
    entry:      float
    stop_loss:  float
    take_profit: float
    quantity:   int
    ai_confidence: float
    status:     str   # OPEN, WIN, LOSS, CLOSED
    exit_price: float = 0.0
    pnl:        float = 0.0
    pnl_pct:    float = 0.0


class SessionLogger:
    """
    Tracks every trade in the current session and writes
    a summary to CSV on shutdown.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.session_start = datetime.now()
        self.trades: list[TradeRecord] = []
        self.signals_found = 0
        self.signals_rejected_ai = 0
        self.signals_rejected_risk = 0

        # Ensure logs directory exists
        Path("logs").mkdir(exist_ok=True)

        # Initialize CSV files with headers if they don't exist
        self._init_csv(TRADES_FILE, [
            "date", "symbol", "direction", "strategy",
            "entry", "stop_loss", "take_profit", "quantity",
            "ai_confidence", "status", "exit_price", "pnl", "pnl_pct", "mode"
        ])
        self._init_csv(SESSIONS_FILE, [
            "date", "start_time", "end_time", "duration_min",
            "mode", "signals_found", "trades_executed",
            "wins", "losses", "open", "win_rate",
            "session_pnl", "total_pnl_alltime"
        ])

        self._print_startup_banner()

    def _print_startup_banner(self):
        """Print session header and all-time stats on startup."""
        now = self.session_start.strftime("%Y-%m-%d %H:%M:%S")
        stats = self._load_alltime_stats()

        lines = [
            "",
            "=" * 60,
            f"  SESSION START  |  {now}  |  {self.mode.upper()}",
            "=" * 60,
        ]

        if stats["total_sessions"] > 0:
            lines += [
                f"  ALL-TIME RECORD ({stats['total_sessions']} sessions)",
                f"  Trades:    {stats['total_trades']}  |  "
                f"Wins: {stats['total_wins']}  |  "
                f"Losses: {stats['total_losses']}",
                f"  Win Rate:  {stats['win_rate']:.0%}",
                f"  Total P&L: ${stats['total_pnl']:+.2f}",
                "-" * 60,
            ]
        else:
            lines.append("  No previous sessions -- this is your first run!")

        lines += [
            "  Watching market for signals...",
            "=" * 60,
            "",
        ]

        for line in lines:
            logger.info(line)

    def record_signal(self, found: int, rejected_ai: int = 0, rejected_risk: int = 0):
        """Call each cycle to accumulate signal counts."""
        self.signals_found += found
        self.signals_rejected_ai += rejected_ai
        self.signals_rejected_risk += rejected_risk

    def record_trade(self, signal) -> TradeRecord:
        """Call when a trade is executed."""
        record = TradeRecord(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol=signal.symbol,
            direction=signal.direction,
            strategy=signal.strategy_name,
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            quantity=signal.quantity,
            ai_confidence=signal.ai_confidence,
            status="OPEN",
        )
        self.trades.append(record)
        logger.info(
            f"[TRADE #{len(self.trades)}] {signal.direction} "
            f"{signal.quantity}x {signal.symbol} @ ${signal.entry_price:.2f} "
            f"| AI: {signal.ai_confidence:.0%} | "
            f"SL: ${signal.stop_loss:.2f} | TP: ${signal.take_profit:.2f}"
        )
        return record

    def close_trade(self, record: TradeRecord, exit_price: float, reason: str):
        """Call when a position is closed (stop loss or take profit)."""
        record.exit_price = exit_price
        record.pnl = (exit_price - record.entry) * record.quantity
        if record.direction == "SELL":
            record.pnl = -record.pnl
        record.pnl_pct = record.pnl / (record.entry * record.quantity)
        record.status = "WIN" if record.pnl > 0 else "LOSS"

        logger.info(
            f"[TRADE CLOSED] {record.symbol} | {record.status} | "
            f"Exit: ${exit_price:.2f} | "
            f"P&L: ${record.pnl:+.2f} ({record.pnl_pct:+.1%}) | "
            f"Reason: {reason}"
        )

    def print_shutdown_summary(self):
        """Print full session summary and save to CSV. Call on bot shutdown."""
        duration = (datetime.now() - self.session_start).seconds // 60
        executed = len(self.trades)
        wins   = sum(1 for t in self.trades if t.status == "WIN")
        losses = sum(1 for t in self.trades if t.status == "LOSS")
        open_  = sum(1 for t in self.trades if t.status == "OPEN")
        pnl    = sum(t.pnl for t in self.trades if t.status in ("WIN", "LOSS"))
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

        logger.info("")
        logger.info("=" * 60)
        logger.info(f"  SESSION SUMMARY  |  {self.mode.upper()}")
        logger.info("=" * 60)
        logger.info(f"  Duration:        {duration} minutes")
        logger.info(f"  Signals found:   {self.signals_found}")
        logger.info(f"  Trades executed: {executed}")
        logger.info(f"  Results:         {wins}W / {losses}L / {open_} open")
        if wins + losses > 0:
            logger.info(f"  Win rate:        {win_rate:.0%}")
        logger.info(f"  Session P&L:     ${pnl:+.2f}")
        logger.info("=" * 60)
        logger.info("")

        # Save trades to CSV
        for trade in self.trades:
            self._append_csv(TRADES_FILE, [
                trade.timestamp, trade.symbol, trade.direction,
                trade.strategy, trade.entry, trade.stop_loss,
                trade.take_profit, trade.quantity, trade.ai_confidence,
                trade.status, trade.exit_price, trade.pnl, trade.pnl_pct,
                self.mode
            ])

        # Load all-time stats for session row
        stats = self._load_alltime_stats()
        total_pnl = stats["total_pnl"] + pnl

        # Save session summary to CSV
        self._append_csv(SESSIONS_FILE, [
            self.session_start.strftime("%Y-%m-%d"),
            self.session_start.strftime("%H:%M:%S"),
            datetime.now().strftime("%H:%M:%S"),
            duration, self.mode, self.signals_found, executed,
            wins, losses, open_, f"{win_rate:.2f}", f"{pnl:.2f}",
            f"{total_pnl:.2f}"
        ])

    def _load_alltime_stats(self) -> dict:
        """Read sessions.csv and compute running totals."""
        defaults = {
            "total_sessions": 0, "total_trades": 0,
            "total_wins": 0, "total_losses": 0,
            "win_rate": 0.0, "total_pnl": 0.0
        }
        if not Path(SESSIONS_FILE).exists():
            return defaults

        try:
            with open(SESSIONS_FILE, newline="") as f:
                rows = list(csv.DictReader(f))

            if not rows:
                return defaults

            total_wins   = sum(int(r.get("wins", 0)) for r in rows)
            total_losses = sum(int(r.get("losses", 0)) for r in rows)
            total_trades = total_wins + total_losses
            win_rate = total_wins / total_trades if total_trades > 0 else 0
            total_pnl = sum(float(r.get("session_pnl", 0)) for r in rows)

            return {
                "total_sessions": len(rows),
                "total_trades":   total_trades,
                "total_wins":     total_wins,
                "total_losses":   total_losses,
                "win_rate":       win_rate,
                "total_pnl":      total_pnl,
            }
        except Exception as e:
            logger.warning(f"Could not load session history: {e}")
            return defaults

    def _init_csv(self, path: str, headers: list):
        if not Path(path).exists():
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(headers)

    def _append_csv(self, path: str, row: list):
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow(row)