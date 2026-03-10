"""
src/utils/logger.py
-------------------
Sets up logging: console output + rotating log file.
"""

import logging
import logging.handlers
import os
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

    # Console handler — force UTF-8 on Windows to avoid emoji/unicode crashes
    import sys
    stream = sys.stdout
    if hasattr(stream, 'reconfigure'):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass
    ch = logging.StreamHandler(stream)
    ch.setLevel(log_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler (10MB max, keep 5 backups)
    fh = logging.handlers.RotatingFileHandler(
        "logs/alphabot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    fh.setLevel(logging.DEBUG)  # Always log DEBUG to file
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger("alphabot")