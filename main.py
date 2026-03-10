"""
AlphaBot - Schwab Automated Trading Bot
Main entry point. Run this file to start the bot.

Usage:
    python main.py --mode paper     # Safe paper trading (recommended to start)
    python main.py --mode live      # Live trading (only after validating strategy)
    python main.py --mode backtest  # Backtest against historical data
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.bot import TradingBot
from src.utils.logger import setup_logger
from config.settings import Settings


def parse_args():
    parser = argparse.ArgumentParser(
        description="AlphaBot - Automated Stock & Options Trading Bot"
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live", "backtest"],
        default="paper",
        help="Trading mode (default: paper)"
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to config file"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Setup logging first
    logger = setup_logger(level=args.log_level)
    logger.info("=" * 60)
    logger.info("  AlphaBot - Automated Trading Bot Starting Up")
    logger.info("=" * 60)
    logger.info(f"  Mode: {args.mode.upper()}")

    if args.mode == "live":
        # Safety confirmation for live trading
        print("\n⚠️  WARNING: You are about to start LIVE trading with REAL money.")
        print("   Make sure you have tested in paper mode first.")
        confirm = input("   Type 'YES I UNDERSTAND' to continue: ")
        if confirm != "YES I UNDERSTAND":
            print("Live trading cancelled.")
            sys.exit(0)

    # Load settings
    settings = Settings(config_path=args.config, mode=args.mode)

    # Initialize and run the bot
    bot = TradingBot(settings=settings)

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
        bot.shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        bot.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
