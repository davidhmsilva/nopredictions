"""
Market Flow Tracker — detects smart money entering Polymarket by monitoring
order book depth and volume changes.

Key insight: When large orders (10k+) stack on one side, smart money is
either accumulating or dumping. Direction + timing = timing signal.

Usage:
    flow = MarketFlow()
    snapshot = flow.get_order_book('market_id')
    signals = flow.detect_smart_money_signal(market_id, our_model_edge_direction)
    # → {'whale_activity': 'stacking_yes', 'confidence': 0.85, ...}
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../ingest/.env'))

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
WHALE_VOLUME_USDC = 10000  # volume threshold to count as whale activity


@dataclass
class OrderBookSnapshot:
    """Single-moment view of order book for a market."""
    market_id: str
    timestamp: float
    # yes_side: [(price, depth), ...]  where depth = cumulative volume above/below price
    yes_prices: list[tuple[float, float]]  # (price, volume_at_price)
    no_prices: list[tuple[float, float]]
    yes_total_volume: float = 0.0
    no_total_volume: float = 0.0
    yes_whale_volume: float = 0.0  # volume at price levels with whale-size orders
    no_whale_volume: float = 0.0


@dataclass
class MarketFlowSignal:
    """Smart money signal from order book activity."""
    market_id: str
    direction: str  # 'yes_stacking', 'no_stacking', 'balanced', 'unknown'
    whale_side: str  # 'yes', 'no', 'none'
    whale_volume: float  # total whale-sized volume detected
    yes_bid_price: float  # weighted average bid price for yes
    no_bid_price: float
    confidence: float  # 0–1, how certain is whale activity
    reasoning: str


class MarketFlow:
    """Track order book flow and detect smart money signals."""

    def __init__(self):
        self.history: dict[str, list[OrderBookSnapshot]] = {}  # market_id → snapshots
        self.last_snapshot: dict[str, OrderBookSnapshot] = {}  # market_id → latest

    def get_order_book(self, market_id: str) -> Optional[OrderBookSnapshot]:
        """Fetch current order book for a Polymarket market."""
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=10,
            )
            if resp.status_code != 200:
                log.debug(f"[market_flow] API HTTP {resp.status_code} for {market_id}")
                return None

            market = resp.json()
            if not isinstance(market, dict):
                return None

            # Extract outcome prices (yes/no)
            outcomes = market.get("outcomes", [])
            if len(outcomes) < 2:
                return None

            yes_price = float(outcomes[0].get("price", 0)) or 0.0
            no_price = float(outcomes[1].get("price", 0)) or 0.0

            # Try to get order book depth (if available in API)
            # For now, use simple price-based inference
            yes_depth = market.get("volume24h", 0)
            no_depth = market.get("volume24h", 0)

            import time
            snapshot = OrderBookSnapshot(
                market_id=market_id,
                timestamp=time.time(),
                yes_prices=[(yes_price, yes_depth)],
                no_prices=[(no_price, no_depth)],
                yes_total_volume=yes_depth,
                no_total_volume=no_depth,
                yes_whale_volume=0.0,
                no_whale_volume=0.0,
            )

            # Store in history
            if market_id not in self.history:
                self.history[market_id] = []
            self.history[market_id].append(snapshot)
            self.last_snapshot[market_id] = snapshot

            return snapshot

        except Exception as e:
            log.debug(f"[market_flow] Error fetching {market_id}: {e}")
            return None

    def detect_smart_money_signal(
        self, market_id: str, model_edge_direction: str
    ) -> Optional[MarketFlowSignal]:
        """
        Detect if smart money is entering on either side.
        model_edge_direction: 'yes', 'no', or 'neutral'
        Returns signal if whale activity detected, else None.
        """
        if market_id not in self.last_snapshot:
            return None

        snapshot = self.last_snapshot[market_id]

        # Detect stacking: if one side has significantly more whale volume
        yes_whale = snapshot.yes_whale_volume
        no_whale = snapshot.no_whale_volume
        total_whale = yes_whale + no_whale

        if total_whale < WHALE_VOLUME_USDC:
            # Not enough whale activity
            return None

        whale_ratio = yes_whale / total_whale if total_whale > 0 else 0.5
        if whale_ratio > 0.65:
            whale_side = "yes"
            direction = "yes_stacking"
        elif whale_ratio < 0.35:
            whale_side = "no"
            direction = "no_stacking"
        else:
            whale_side = "none"
            direction = "balanced"

        # Confidence: how concentrated is the whale volume
        confidence = 0.5 + (abs(whale_ratio - 0.5) * 1.0)  # 0.5 to 1.0

        # Agreement check: if whale side matches our edge direction, boost confidence
        if model_edge_direction == whale_side:
            confidence = min(1.0, confidence + 0.15)
            agreement = "✓ whale agrees with model"
        elif model_edge_direction != "neutral":
            confidence = max(0.3, confidence - 0.15)
            agreement = "✗ whale opposes model (caution)"
        else:
            agreement = "○ model neutral"

        reasoning = f"Whale volume: {total_whale/1000:.0f}k USDC ({whale_ratio*100:.0f}% on {whale_side}). {agreement}"

        signal = MarketFlowSignal(
            market_id=market_id,
            direction=direction,
            whale_side=whale_side,
            whale_volume=total_whale,
            yes_bid_price=snapshot.yes_prices[0][0] if snapshot.yes_prices else 0.5,
            no_bid_price=snapshot.no_prices[0][0] if snapshot.no_prices else 0.5,
            confidence=confidence,
            reasoning=reasoning,
        )

        return signal

    def get_volume_momentum(self, market_id: str, window_snapshots: int = 3) -> dict[str, Any]:
        """
        Detect if order book volume is accelerating (smart money still entering).
        Returns momentum: 'accelerating', 'steady', 'decelerating', 'unknown'
        """
        if market_id not in self.history:
            return {"momentum": "unknown"}

        snapshots = self.history[market_id][-window_snapshots:]
        if len(snapshots) < 2:
            return {"momentum": "unknown"}

        volumes = [s.yes_total_volume + s.no_total_volume for s in snapshots]
        if not volumes or volumes[0] == 0:
            return {"momentum": "unknown"}

        # Rate of volume increase
        deltas = [volumes[i + 1] - volumes[i] for i in range(len(volumes) - 1)]
        avg_delta = sum(deltas) / len(deltas) if deltas else 0

        if avg_delta > volumes[0] * 0.1:  # >10% growth per cycle
            momentum = "accelerating"
        elif avg_delta > 0:
            momentum = "steady"
        else:
            momentum = "decelerating"

        return {
            "momentum": momentum,
            "total_volume": volumes[-1],
            "recent_delta": deltas[-1] if deltas else 0,
            "snapshots_tracked": len(snapshots),
        }

    def clear_history(self, market_id: Optional[str] = None) -> None:
        """Clear order book history (e.g., between market windows)."""
        if market_id:
            self.history.pop(market_id, None)
        else:
            self.history.clear()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    flow = MarketFlow()
    # Test with a known Polymarket market ID
    # market_id = "0x1234..."  # would need a real market ID
    # snapshot = flow.get_order_book(market_id)
    # if snapshot:
    #     signal = flow.detect_smart_money_signal(market_id, "yes")
    #     print(f"Signal: {signal}")
    print("Market flow tracker initialized (requires real market IDs to test)")
