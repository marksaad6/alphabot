# AlphaBot ⚡
**Automated Stock & Options Trading Bot**  
Powered by Charles Schwab API + Claude AI

---

## What Is This?

AlphaBot connects to your Schwab/ThinkorSwim account and automatically trades stocks and options every 5 minutes during market hours — even while you're at work.

Three layers work together on every signal:

| Layer | File | What It Does |
|---|---|---|
| Strategy Engine | `src/strategies/` | Scans watchlist for momentum, mean-reversion, and options setups |
| AI Filter | `src/ai/analyzer.py` | Claude reviews each signal — only 70%+ confidence trades execute |
| Risk Manager | `src/risk_manager.py` | Enforces position sizing, stop-loss, daily trade limits |

> **Disclaimer:** Trading involves significant risk. Always paper trade first. Never risk money you cannot afford to lose.

---

## Project Structure

```
alphabot/
├── main.py                      ← Run this to start the bot
├── requirements.txt             ← Python packages
├── .env.example                 ← Copy to .env and fill in your keys
├── .gitignore
│
├── config/
│   ├── settings.py              ← Configuration dataclasses
│   └── settings.yaml            ← Tunable settings (edit this)
│
├── src/
│   ├── bot.py                   ← Main orchestrator
│   ├── schwab_client.py         ← Schwab API wrapper
│   ├── risk_manager.py          ← Trade approval + position sizing
│   ├── portfolio.py             ← Tracks cash and open positions
│   ├── session_logger.py        ← Session summaries + trade history CSV
│   │
│   ├── strategies/
│   │   ├── momentum.py          ← Buy uptrend pullbacks
│   │   ├── mean_reversion.py    ← Buy oversold dips
│   │   └── options_theta.py     ← Sell cash-secured puts for income
│   │
│   ├── ai/
│   │   ├── analyzer.py          ← Claude AI signal validation
│   │   └── credit_monitor.py    ← Monitors Anthropic credits, auto-fallback
│   │
│   └── utils/
│       ├── logger.py            ← Console + rotating file logging
│       └── market_hours.py      ← Market open/close detection
│
└── logs/
    ├── alphabot.log             ← Full activity log
    ├── trades.csv               ← Every trade recorded
    └── sessions.csv             ← Session summaries + running P&L
```

---

## Setup

### 1. Requirements
- Python 3.11+
- Charles Schwab brokerage account
- Anthropic API account ($5 minimum credits)

### 2. Install

```bash
git clone https://github.com/YOUR_USERNAME/alphabot.git
cd alphabot
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Schwab Developer App

1. Go to [developer.schwab.com](https://developer.schwab.com) and sign in
2. Click **Add an App** and fill in:

| Field | Value |
|---|---|
| App Name | AlphaBot |
| App Type | Personal |
| Callback URL | `https://127.0.0.1` |
| Default Scopes | Check all (Trading, Account, MarketData) |
| Order Rate Limit | **60 requests/minute** |

3. Copy your **App Key** and **App Secret**
4. Find your account number at schwab.com → Account Details

> **Note:** Schwab's API connects to real brokerage accounts only. PaperMoney inside ThinkorSwim is not accessible via API. AlphaBot handles this by simulating trades internally in `--mode paper` using real live market data — no real orders are ever sent in paper mode.

### 4. Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Add at least $5 in credits under **Settings → Billing**
3. Create a key under **API Keys** and copy it

### 5. Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your real values:

```
SCHWAB_APP_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
SCHWAB_CALLBACK_URL=https://127.0.0.1
SCHWAB_ACCOUNT_NUMBER=your_8_digit_account_number
ANTHROPIC_API_KEY=sk-ant-your_key
```

> Never commit `.env` to Git — it's already in `.gitignore`

### 6. First Run + Authentication

```bash
python main.py --mode paper
```

On first run, Schwab will prompt OAuth authorization:
1. A URL prints in the terminal — open it in your browser
2. Log into Schwab and click **Allow**
3. You'll be redirected to `https://127.0.0.1/?code=...`
4. Copy that **entire redirect URL** and paste it back into the terminal

Tokens save to `config/tokens.json` automatically. Schwabdev refreshes them — you only do this once. Re-authenticate every 7 days (recommend every Sunday).

---

## Running the Bot

| Command | What It Does | When to Use |
|---|---|---|
| `python main.py --mode paper` | Simulated trades, real market data, $5,000 virtual cash | Always start here — minimum 4 weeks |
| `python main.py --mode live` | Real trades on your Schwab account | Only after validating paper performance |
| `python main.py --log-level DEBUG` | Shows per-symbol scan scoring | Diagnosing why signals fire or don't |

### Switching to Live

1. Paper trade for at least 4 weeks
2. Confirm 60%+ win rate over 30+ trades in `logs/sessions.csv`
3. Set `SCHWAB_ACCOUNT_NUMBER` in `.env` to your real account number
4. Run `python main.py --mode live`
5. Type `YES I UNDERSTAND` when prompted

> Start with $500–$1,000 real capital before deploying full amount.

### What a Working Session Looks Like

```
============================================================
  SESSION START  |  2026-03-11 09:31:00  |  PAPER
============================================================
  ALL-TIME RECORD (3 sessions)
  Trades:    12  |  Wins: 9  |  Losses: 3
  Win Rate:  75%
  Total P&L: +$847.20
------------------------------------------------------------
  Watching market for signals...
============================================================

INFO  src.bot        --- Running scan cycle ---
INFO  src.bot        Portfolio: $5,000.00 cash | 0 open positions
INFO  momentum       [SIGNAL] Momentum signal: BUY QQQ @ $607.76
INFO  ai.analyzer    [OK] AI approved QQQ (BUY) -- confidence: 78%
INFO  risk_manager   [OK] Risk approved: QQQ | Qty: 4 | Value: $2,431
INFO  schwab         [PAPER TRADE] BUY 4x QQQ @ $607.76 | ID: PAPER-48291
INFO  src.bot        [GREEN] TAKE PROFIT: Closing QQQ at $632.07 (gain: 4.0%)
```

### Why 0 Signals Is Normal

- Bot only trades **9:30 AM – 4:00 PM Eastern**, Monday–Friday
- Strategy conditions are intentionally strict — this is what prevents bad trades
- AI filter rejects signals below 70% confidence
- During broad market downtrends, momentum signals won't fire by design

---

## The 3 Strategies

### Momentum (`momentum.py`)
Buy stocks in strong uptrends during small pullbacks.  
**Conditions (needs 4 of 6):** Price above SMA20 and SMA50, SMA20 > SMA50, RSI 50–70, volume above average, price near EMA20.  
**Target win rate:** 60–65% base, 70%+ with AI filter.

### Mean Reversion (`mean_reversion.py`)
Buy oversold dips in fundamentally healthy stocks.  
**Conditions (needs 2 of 4):** Above SMA200, RSI < 45, down 2%+ in 3 days, near Bollinger lower band.  
**Target win rate:** 62–68% base, 70%+ with AI filter.

### Theta / Cash-Secured Puts (`options_theta.py`)
Sell put options on quality stocks to collect premium income.  
**Setup:** 30–45 DTE, ~0.20 delta, liquid large-caps.  
**Target win rate:** 70–80% by design (selling 20-delta options).

---

## Risk Management

All rules enforced automatically. Configurable in `config/settings.yaml`.

| Rule | Default | Purpose |
|---|---|---|
| Max position size | 5% of portfolio | Caps loss on any single trade |
| Max total exposure | 50% deployed | Always keeps dry powder |
| Stop loss | 2% per trade | Auto-closes losers |
| Take profit | 4% target | 2:1 reward/risk ratio |
| Max daily trades | 5 | Prevents overtrading + PDT |
| Min cash reserve | $500 | Never goes all-in |

**PDT Warning:** Accounts under $25,000 are limited to 3 day trades per rolling 5-day window. Hold positions overnight (swing trade) to avoid violations. The options strategy is never affected by PDT.

---

## Configuration

Edit `config/settings.yaml` — no code changes needed:

```yaml
risk:
  max_position_size_pct: 0.05
  stop_loss_pct:         0.02
  take_profit_pct:       0.04
  max_daily_trades:      5
  min_cash_reserve:      500.0

strategy:
  use_momentum:            true
  use_mean_reversion:      true
  use_cash_secured_puts:   true
  ai_confidence_threshold: 0.70   # Raise to 0.75 for fewer, higher quality trades

  stock_watchlist:
    - SPY
    - QQQ
    - AAPL
    - MSFT
    - NVDA
    - AMZN
    - GOOGL
    - META
    - TSLA
    - AMD
```

---

## AI Credit Monitor

The bot monitors your Anthropic credit balance automatically:

- **Checks on startup** and every 30 minutes during the session
- **`[CREDIT LOW]`** warning logged when balance drops below $2.00
- **`[CREDIT EMPTY]`** — instead of crashing, switches to a rule-based fallback filter so the session continues safely
- Add credits at [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing)

---

## Session Logging

Every session prints a summary on startup and shutdown:

```
============================================================
  SESSION SUMMARY  |  PAPER
============================================================
  Duration:        387 minutes
  Signals found:   8
  Trades executed: 3
  Results:         2W / 1L / 0 open
  Win rate:        67%
  Session P&L:     +$124.50
============================================================
```

All trades saved to `logs/trades.csv`. All sessions saved to `logs/sessions.csv`. Open either in Excel to track progress toward your 75% win rate goal.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `app_key cannot be None` | `.env` not loaded | Add `load_dotenv()` to top of `main.py` |
| `callback_url must be https` | Missing env var | Add `SCHWAB_CALLBACK_URL=https://127.0.0.1` to `.env` |
| `UnicodeEncodeError cp1252` | Emoji in log on Windows | Replace emoji chars in `.py` files with plain text like `[PAPER]` |
| `Client has no attribute X` | schwabdev version mismatch | Run `python -c "import schwabdev; print(dir(schwabdev.Client))"` and use exact method name |
| `Portfolio: $0.00 cash` | Wrong account number | Check `SCHWAB_ACCOUNT_NUMBER` in `.env`. Paper mode uses $5,000 simulated cash automatically. |
| `0 signals every cycle` | Normal outside market hours | Market open 9:30 AM–4:00 PM Eastern Mon–Fri only |
| `[CREDIT EMPTY]` on startup | Anthropic key has no credits | Add credits at console.anthropic.com → Settings → Billing |
| Token expired | Schwab tokens expire every 7 days | Re-run the bot — it will prompt re-authentication |

---

## Roadmap

| Phase | Timeline | Goal |
|---|---|---|
| 1 - Paper Validate | Weeks 1–4 | Run daily, review logs each evening |
| 2 - Win Rate Check | Weeks 5–8 | Track trades in sessions.csv, target 70%+ over 50 trades |
| 3 - Go Live Small | Month 3 | Deploy $500–$1,000 real capital |
| 4 - Scale Up | Month 4–6 | Increase capital, target $200–$500/month |
| 5 - Main Income | Month 6–12 | Full capital, $1,500–$3,000/month target |

**Planned features:**
- Telegram/Discord trade alerts
- Web dashboard (real-time P&L + positions)
- Backtesting engine (5 years historical data)
- Earnings date filter (auto-skip around earnings)
- News sentiment feed into AI analysis

---

## Security Checklist

- [ ] `.env` is in `.gitignore` and never committed
- [ ] `config/tokens.json` is in `.gitignore`  
- [ ] No API keys hardcoded in any `.py` file
- [ ] Paper traded for minimum 4 weeks before live
- [ ] Emergency fund separate from this trading account
- [ ] Starting live with $500–$1,000 max until strategy validated

---

*Built with Schwab API + Claude AI*
