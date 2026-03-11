"""
src/ai/analyzer.py
------------------
Claude AI signal validator -- upgraded with:
  1. News feed + earnings guard + market breadth context
  2. Professional trader prompt framework
  3. Structured scoring rubric (not just a gut-feel confidence score)
  4. Automatic fallback when credits run out

The prompt now instructs Claude to evaluate 5 specific dimensions:
  - Trend alignment    (is this trade WITH the market, not against it?)
  - Setup quality      (how clean is the technical pattern?)
  - Risk/reward        (is the R:R worth taking?)
  - News/event risk    (any upcoming catalyst that could blow the trade up?)
  - Market conditions  (is the broader environment supportive?)

Each dimension scored 0-10, then weighted into a final confidence score.
This produces far more consistent, explainable decisions than a raw number.
"""

import logging
import json
from dataclasses import dataclass
from anthropic import Anthropic

from config.settings import AIConfig
from src.ai.credit_monitor import CreditMonitor
from src.news_feed import NewsFeed

logger = logging.getLogger(__name__)


@dataclass
class AIAnalysis:
    confidence: float
    reasoning: str
    risk_factors: list
    recommended_action: str
    score_breakdown: dict = None   # New: per-dimension scores
    used_fallback: bool = False


class AIAnalyzer:

    def __init__(self, config: AIConfig):
        self.config = config
        self.client = Anthropic(api_key=config.api_key)
        self.credit_monitor = CreditMonitor(api_key=config.api_key)
        self.news_feed = NewsFeed()

        status = self.credit_monitor.check(force=True)
        if status.fallback_active:
            logger.warning(
                "AI Analyzer starting in FALLBACK mode -- no credits. "
                "Add credits at console.anthropic.com/settings/billing"
            )
        else:
            logger.info("AI Analyzer initialized (Claude + news feed active)")

    def analyze_signal(self, signal, schwab_client) -> AIAnalysis:
        status = self.credit_monitor.check()
        if status.fallback_active:
            return self._fallback_analysis(signal)

        # ── 1. Price history ──────────────────────────────────
        try:
            candles = schwab_client.get_price_history(
                symbol=signal.symbol,
                period_type="day",
                period=self.config.context_lookback_days,
                frequency_type="minute",
                frequency=30,
            )
            price_summary = self._summarize_candles(candles)
        except Exception as e:
            logger.warning(f"Could not fetch price history: {e}")
            price_summary = "Price history unavailable"

        # ── 2. News + earnings + market breadth ───────────────
        try:
            context = self.news_feed.get_full_context(signal.symbol, schwab_client)
        except Exception as e:
            logger.warning(f"News context failed: {e}")
            context = "News context unavailable"

        # ── 3. Build prompt and call Claude ───────────────────
        prompt = self._build_prompt(signal, price_summary, context)

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=1200,
                system=self._system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            analysis = self._parse_response(response.content[0].text)

            # Log a clean summary
            logger.info(
                f"[AI] {signal.symbol}: {analysis.recommended_action} | "
                f"confidence={analysis.confidence:.0%} | "
                f"{analysis.reasoning[:100]}"
            )
            if analysis.score_breakdown:
                logger.debug(f"[AI] Score breakdown: {analysis.score_breakdown}")
            if analysis.risk_factors:
                logger.debug(f"[AI] Risk factors: {analysis.risk_factors}")

            return analysis

        except Exception as e:
            error_str = str(e).lower()
            if "credit balance is too low" in error_str or "insufficient" in error_str:
                logger.error(
                    "[CREDIT EMPTY] Credits exhausted mid-session. "
                    "Switching to fallback. "
                    "Add credits at console.anthropic.com/settings/billing"
                )
                self.credit_monitor._status.fallback_active = True
                self.credit_monitor._status.is_empty = True
                return self._fallback_analysis(signal)

            logger.error(f"AI analysis failed: {e}")
            return AIAnalysis(
                confidence=0.0,
                reasoning=f"AI error: {e}",
                risk_factors=["AI unavailable"],
                recommended_action="SKIP",
            )

    # ── System Prompt ─────────────────────────────────────────
    def _system_prompt(self) -> str:
        return """You are a professional quantitative trader with 20 years of experience
managing a $10M portfolio. You evaluate algorithmic trading signals with discipline
and rigorous risk management.

Your job: review each signal and return a JSON decision.

SCORING FRAMEWORK -- evaluate each dimension 0-10:
  trend_alignment:   Is this trade WITH the dominant trend? (0=fighting trend, 10=perfect alignment)
  setup_quality:     How clean/textbook is the technical pattern? (0=messy, 10=perfect)
  risk_reward:       Is the R:R ratio worth the risk? (0=bad ratio, 10=excellent ratio)
  event_risk:        Any news/earnings/macro events that could blow this up? (0=major event imminent, 10=clear calendar)
  market_conditions: Is the overall market environment supportive? (0=bear/fearful, 10=bull/calm)

FINAL CONFIDENCE = weighted average:
  trend_alignment   x 0.30  (most important -- don't fight the tape)
  setup_quality     x 0.25
  risk_reward       x 0.20
  event_risk        x 0.15  (earnings gaps kill accounts)
  market_conditions x 0.10

HARD RULES (override scoring):
  - If earnings within 3 days: cap confidence at 0.40 maximum
  - If VIX > 30: cap confidence at 0.55 maximum
  - If price below SMA50 by more than 5%: cap confidence at 0.45 for BUY signals
  - If macro event TODAY: cap confidence at 0.35 maximum

RESPONSE FORMAT -- JSON only, no other text:
{
  "scores": {
    "trend_alignment": 7,
    "setup_quality": 6,
    "risk_reward": 8,
    "event_risk": 9,
    "market_conditions": 5
  },
  "confidence": 0.71,
  "reasoning": "One clear sentence explaining the decision",
  "risk_factors": ["specific risk 1", "specific risk 2"],
  "recommended_action": "EXECUTE"
}

recommended_action options:
  EXECUTE      -- confidence >= 0.70, take the trade at full size
  REDUCE_SIZE  -- confidence 0.55-0.69, take at half position size
  SKIP         -- confidence < 0.55, do not trade

Be conservative. Missing a good trade is better than taking a bad one.
In bear markets, the default should be SKIP unless setup is exceptional."""

    # ── User Prompt ───────────────────────────────────────────
    def _build_prompt(self, signal, price_summary: str, context: str) -> str:
        rr = signal.take_profit_pct / signal.stop_loss_pct if signal.stop_loss_pct > 0 else 0

        return f"""TRADE SIGNAL TO EVALUATE:
============================================================
Symbol:    {signal.symbol}
Direction: {signal.direction}
Strategy:  {signal.strategy_name}
Entry:     ${signal.entry_price:.2f}
Stop Loss: ${signal.stop_loss:.2f}  ({signal.stop_loss_pct:.1%} risk)
Target:    ${signal.take_profit:.2f} ({signal.take_profit_pct:.1%} gain)
R:R Ratio: {rr:.1f}:1
Asset:     {signal.asset_type}

STRATEGY'S REASONING:
{signal.reasoning}

============================================================
RECENT PRICE ACTION (30-min candles, last 5 days):
{price_summary}

============================================================
NEWS, EARNINGS & MARKET CONTEXT:
{context}

============================================================
Evaluate this signal using the 5-dimension scoring framework.
Return JSON only."""

    # ── Candle Summary ────────────────────────────────────────
    def _summarize_candles(self, candles: list) -> str:
        if not candles:
            return "No data available"

        recent = candles[-16:]  # Last 16 x 30min = ~2 trading days
        lines = []
        for c in recent:
            lines.append(
                f"  Open ${c['open']:.2f} | High ${c['high']:.2f} | "
                f"Low ${c['low']:.2f} | Close ${c['close']:.2f} | "
                f"Vol {c['volume']:,}"
            )

        # Overall trend context
        if len(candles) >= 20:
            week_ago = candles[-20]["close"]
            now = candles[-1]["close"]
            change = (now - week_ago) / week_ago
            high_5d = max(c["high"] for c in candles[-20:])
            low_5d  = min(c["low"]  for c in candles[-20:])
            trend_line = (
                f"5-day trend: {change:+.1%} | "
                f"Range: ${low_5d:.2f} - ${high_5d:.2f} | "
                f"Current: ${now:.2f} ({(now-low_5d)/(high_5d-low_5d)*100:.0f}% of range)"
            )
        else:
            trend_line = "Insufficient history"

        return trend_line + "\n" + "\n".join(lines)

    # ── Parse Response ────────────────────────────────────────
    def _parse_response(self, raw: str) -> AIAnalysis:
        clean = raw.strip()
        # Strip markdown code fences if present
        if "```" in clean:
            parts = clean.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    clean = part
                    break

        data = json.loads(clean.strip())

        scores = data.get("scores", {})
        confidence = float(data.get("confidence", 0.0))

        # Recalculate confidence from scores if provided (sanity check)
        if scores:
            weights = {
                "trend_alignment":   0.30,
                "setup_quality":     0.25,
                "risk_reward":       0.20,
                "event_risk":        0.15,
                "market_conditions": 0.10,
            }
            calc = sum(scores.get(k, 5) / 10 * w for k, w in weights.items())
            # Use the higher of model's stated confidence or calculated
            # (model may have applied hard caps)
            confidence = min(confidence, calc * 1.05)  # Allow slight variance

        return AIAnalysis(
            confidence=round(confidence, 3),
            reasoning=data.get("reasoning", ""),
            risk_factors=data.get("risk_factors", []),
            recommended_action=data.get("recommended_action", "SKIP"),
            score_breakdown=scores,
        )

    # ── Fallback ──────────────────────────────────────────────
    def _fallback_analysis(self, signal) -> AIAnalysis:
        risk_pct   = signal.stop_loss_pct
        reward_pct = signal.take_profit_pct
        rr_ratio   = reward_pct / risk_pct if risk_pct > 0 else 0

        # Hard block: never trade within 3 days of earnings in fallback
        from src.news_feed import EARNINGS_CALENDAR
        from datetime import datetime
        today = datetime.now().date()
        for sym, date_str in EARNINGS_CALENDAR:
            if sym.upper() == signal.symbol.upper() and date_str:
                try:
                    ed = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if 0 <= (ed - today).days <= 3:
                        return AIAnalysis(
                            confidence=0.0,
                            reasoning=f"Fallback SKIP: earnings in {(ed-today).days} days",
                            risk_factors=["Earnings gap risk"],
                            recommended_action="SKIP",
                            used_fallback=True,
                        )
                except ValueError:
                    pass

        if risk_pct > 0.03:
            confidence, action = 0.0, "SKIP"
            reasoning = f"Fallback SKIP: risk {risk_pct:.1%} exceeds 3%"
        elif rr_ratio < 1.5:
            confidence, action = 0.0, "SKIP"
            reasoning = f"Fallback SKIP: R/R {rr_ratio:.1f}x below 1.5 minimum"
        else:
            confidence, action = 0.65, "REDUCE_SIZE"
            reasoning = f"Fallback APPROVE: risk {risk_pct:.1%}, R/R {rr_ratio:.1f}x"

        logger.info(f"[FALLBACK] {signal.symbol}: {action} (confidence={confidence:.0%})")
        return AIAnalysis(
            confidence=confidence,
            reasoning=reasoning,
            risk_factors=["AI fallback -- Claude unavailable"],
            recommended_action=action,
            used_fallback=True,
        )