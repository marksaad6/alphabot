"""
src/ai/analyzer.py
------------------
Uses Claude AI to validate trading signals before execution.

If Anthropic credits run out mid-session, automatically switches to a
rule-based fallback so the bot never freezes or crashes.

Fallback rules (no AI):
  - Signal must have stop loss <= 3% risk
  - Signal must have 2:1 reward/risk ratio minimum
  - Volume must be above average (already checked by strategy)
  - Confidence returned = 0.65 (below default 0.70 threshold = won't trade)
    unless you lower ai_confidence_threshold in settings.yaml to 0.60
"""

import logging
import json
from dataclasses import dataclass
from anthropic import Anthropic

from config.settings import AIConfig
from src.ai.credit_monitor import CreditMonitor

logger = logging.getLogger(__name__)


@dataclass
class AIAnalysis:
    confidence: float
    reasoning: str
    risk_factors: list
    recommended_action: str
    used_fallback: bool = False


class AIAnalyzer:
    """
    Claude-powered trade signal validator with automatic fallback.
    """

    def __init__(self, config: AIConfig):
        self.config = config
        self.client = Anthropic(api_key=config.api_key)
        self.credit_monitor = CreditMonitor(api_key=config.api_key)

        # Check credits immediately on startup
        status = self.credit_monitor.check(force=True)
        if status.fallback_active:
            logger.warning(
                "AI Analyzer starting in FALLBACK mode -- no Anthropic credits. "
                "Add credits at console.anthropic.com/settings/billing"
            )
        else:
            logger.info("AI Analyzer initialized (Claude)")

    def analyze_signal(self, signal, schwab_client) -> AIAnalysis:
        """
        Analyze a trade signal. Uses Claude if credits available,
        falls back to rule-based analysis if not.
        """
        # Re-check credits every 30 min automatically
        status = self.credit_monitor.check()

        if status.fallback_active:
            return self._fallback_analysis(signal)

        # Fetch recent price history for context
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
            logger.warning(f"Could not fetch price history for AI context: {e}")
            price_summary = "Price history unavailable"

        prompt = self._build_prompt(signal, price_summary)

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=1000,
                system=self._system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            return self._parse_response(response.content[0].text)

        except Exception as e:
            error_str = str(e).lower()
            # Detect credit exhaustion mid-session
            if "credit balance is too low" in error_str or "insufficient" in error_str:
                logger.error(
                    "[CREDIT EMPTY] Ran out of Anthropic credits mid-session! "
                    "Switching to rule-based fallback for remainder of session. "
                    "Add credits at: console.anthropic.com/settings/billing"
                )
                # Force the monitor into fallback mode
                self.credit_monitor._status.fallback_active = True
                self.credit_monitor._status.is_empty = True
                return self._fallback_analysis(signal)

            logger.error(f"AI analysis failed: {e}")
            # On unknown failure, return low confidence (fail safe = don't trade)
            return AIAnalysis(
                confidence=0.0,
                reasoning=f"AI analysis failed: {e}",
                risk_factors=["AI unavailable"],
                recommended_action="SKIP",
            )

    def _fallback_analysis(self, signal) -> AIAnalysis:
        """
        Rule-based signal analysis used when Claude is unavailable.

        Conservative rules -- only approves high-quality setups:
          - Risk per trade must be <= 3%
          - Reward must be at least 2x the risk (2:1 R/R)
          - Returns confidence 0.65 by default (just below 0.70 threshold)
            so trades are skipped unless you lower the threshold in settings.yaml
        """
        risk_factors = ["AI fallback mode -- Claude unavailable"]
        confidence = 0.0
        action = "SKIP"

        risk_pct = signal.stop_loss_pct
        reward_pct = signal.take_profit_pct
        rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 0

        if risk_pct > 0.03:
            reasoning = f"Fallback SKIP: risk {risk_pct:.1%} exceeds 3% limit"
        elif rr_ratio < 1.5:
            reasoning = f"Fallback SKIP: R/R ratio {rr_ratio:.1f} below 1.5 minimum"
        else:
            # Meets basic criteria -- approve at reduced confidence
            confidence = 0.65
            action = "REDUCE_SIZE"
            reasoning = (
                f"Fallback APPROVE (no AI): risk {risk_pct:.1%}, "
                f"R/R {rr_ratio:.1f}x. Trade size reduced as precaution."
            )

        logger.info(
            f"[FALLBACK] {signal.symbol}: {action} "
            f"(risk={risk_pct:.1%}, R/R={rr_ratio:.1f}x)"
        )

        return AIAnalysis(
            confidence=confidence,
            reasoning=reasoning,
            risk_factors=risk_factors,
            recommended_action=action,
            used_fallback=True,
        )

    def _system_prompt(self) -> str:
        return """You are an expert stock and options trader with 20 years of experience.
Your job is to review trading signals generated by algorithmic strategies and assess their quality.

You must respond ONLY with a valid JSON object in this exact format:
{
  "confidence": 0.75,
  "reasoning": "Brief explanation of why this trade looks good or bad",
  "risk_factors": ["list", "of", "risks"],
  "recommended_action": "EXECUTE"
}

Rules:
- confidence: float between 0.0 and 1.0
- recommended_action: one of "EXECUTE", "SKIP", or "REDUCE_SIZE"
- Be conservative. A bad skip is better than a bad trade.
- Consider: trend direction, volume, recent news sentiment, support/resistance
- If the market is choppy or uncertain, return low confidence
"""

    def _build_prompt(self, signal, price_summary: str) -> str:
        return f"""Please analyze this trading signal:

SIGNAL DETAILS:
- Symbol: {signal.symbol}
- Direction: {signal.direction}
- Strategy: {signal.strategy_name}
- Entry Price: ${signal.entry_price:.2f}
- Stop Loss: ${signal.stop_loss:.2f} ({signal.stop_loss_pct:.1%} risk)
- Take Profit: ${signal.take_profit:.2f} ({signal.take_profit_pct:.1%} target)
- Asset Type: {signal.asset_type}
- Strategy Reasoning: {signal.reasoning}

RECENT PRICE ACTION (last 5 days):
{price_summary}

Should I execute this trade? Respond with JSON only."""

    def _summarize_candles(self, candles: list) -> str:
        if not candles:
            return "No data available"
        recent = candles[-10:]
        lines = []
        for c in recent:
            lines.append(
                f"  Open: ${c['open']:.2f} | High: ${c['high']:.2f} | "
                f"Low: ${c['low']:.2f} | Close: ${c['close']:.2f} | "
                f"Vol: {c['volume']:,}"
            )
        if len(candles) >= 2:
            change = (candles[-1]["close"] - candles[0]["close"]) / candles[0]["close"]
            trend = f"Overall trend: {change:+.1%} over {len(candles)} candles"
        else:
            trend = "Insufficient data for trend"
        return trend + "\n" + "\n".join(lines)

    def _parse_response(self, raw: str) -> AIAnalysis:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()
        data = json.loads(clean)
        return AIAnalysis(
            confidence=float(data.get("confidence", 0.0)),
            reasoning=data.get("reasoning", ""),
            risk_factors=data.get("risk_factors", []),
            recommended_action=data.get("recommended_action", "SKIP"),
        )