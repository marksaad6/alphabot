"""
src/utils/logger.py
-------------------
Sets up logging: console output + rotating log file.
Forces UTF-8 on Windows to prevent cp1252 encoding errors.
"""

import logging
import logging.handlers
import sys
import io
from pathlib import Path


def setup_logger(level: str = "INFO") -> logging.Logger:
    """Configure root logger with console + file handlers."""
    Path("logs").mkdir(exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(log_level)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-25s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # ── Console handler ─────────────────────────────────────────
    # Force UTF-8 on Windows to prevent cp1252 crashes on special chars
    try:
        # Python 3.7+ on Windows: reconfigure stdout to UTF-8
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        console_stream = sys.stdout
    except Exception:
        # Fallback: wrap stdout with UTF-8 writer
        console_stream = io.TextIOWrapper(
            sys.stdout.buffer, encoding='utf-8', errors='replace'
        )

    ch = logging.StreamHandler(console_stream)
    ch.setLevel(log_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # ── Rotating file handler (10MB, keep 5 backups) ────────────
    fh = logging.handlers.RotatingFileHandler(
        "logs/alphabot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',   # Always UTF-8 in the log file
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger("alphabot")