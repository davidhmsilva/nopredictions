#!/usr/bin/env python3
"""np-risk — bankroll & position sizing for NOPREDICTIONS.

Kelly sizing from a fair probability + executable price, deliberately fractional
because our edge is UNPROVEN — full Kelly on a mis-estimated edge is a fast road
to ruin. Also reports current live exposure vs bankroll.

Usage:
  python risk_calc.py --fair 0.55 --price 0.45 --bankroll 20     # size one bet
  python risk_calc.py --exposure                                  # live exposure report
"""
import argparse, os, sys

def kelly(fair: float, price: float) -> float:
    """Optimal fraction of bankroll for buying YES at `price` if true prob is `fair`.
    Decimal odds o = 1/price; f* = (fair*o - 1)/(o - 1) = (fair - price)/(1 - price)."""
    if not (0 < price < 1) or not (0 < fair < 1):
        return 0.0
    return max(0.0, (fair - price) / (1.0 - price))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fair", type=float, help="our fair/model probability 0..1")
    ap.add_argument("--price", type=float, help="executable PM price (ask) 0..1")
    ap.add_argument("--bankroll", type=float, default=20.0)
    ap.add_argument("--kelly-fraction", type=float, default=0.25,
                    help="fraction of full Kelly to actually bet (default 0.25 = quarter)")
    ap.add_argument("--max-bet-frac", type=float, default=0.05,
                    help="hard cap: never stake more than this fraction of bankroll (default 5%)")
    ap.add_argument("--exposure", action="store_true", help="report live exposure from DB instead")
    a = ap.parse_args()

    if a.exposure:
        import psycopg2, psycopg2.extras
        from dotenv import load_dotenv
        REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        load_dotenv(os.path.join(REPO, "ingest", ".env"))
        c = psycopg2.connect(os.getenv("DATABASE_URL")); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(pm_order_size*pm_order_price),0) notional
            FROM paper_trades WHERE pm_live=TRUE AND pm_order_status IN ('live','matched')
              AND (result IS NULL OR result NOT IN ('won','lost','void'))""")
        r = cur.fetchone(); c.close()
        print(f"Open live positions: {r['n']}  |  notional at risk: ${float(r['notional']):.2f}")
        print(f"On a ${a.bankroll:.0f} bankroll that is {float(r['notional'])/a.bankroll*100:.0f}% deployed.")
        print("Guidance: keep total open notional < ~30-40% of bankroll; survive a losing run.")
        return

    if a.fair is None or a.price is None:
        ap.error("provide --fair and --price (or use --exposure)")
    f_full = kelly(a.fair, a.price)
    edge_pp = (a.fair - a.price) * 100
    odds = 1.0 / a.price
    frac_stake_bankroll = min(f_full * a.kelly_fraction, a.max_bet_frac)
    stake_usd = frac_stake_bankroll * a.bankroll

    print(f"Edge: {edge_pp:+.1f}pp  (fair {a.fair:.2%} vs price {a.price:.2%}, decimal odds {odds:.2f})")
    print(f"Full Kelly:        {f_full*100:5.1f}% of bankroll  (${f_full*a.bankroll:.2f})  <- assumes you KNOW the edge")
    print(f"{int(a.kelly_fraction*100)}% Kelly (capped {int(a.max_bet_frac*100)}%): "
          f"{frac_stake_bankroll*100:5.1f}% of bankroll  (${stake_usd:.2f})  <- recommended")
    print(f"Current project default: 1u flat (${min(2.5, 1.0):.2f}-ish per order)")
    print()
    if edge_pp <= 0:
        print("⚠️  No positive edge at this price — do not bet.")
    elif edge_pp > 20:
        print("⚠️  Edge >20pp almost always means model error, not edge (see live audit). "
              "Demand a sharp line to validate before sizing up.")
    print("Note: edge here is UNPROVEN (no consistent +CLV over 200+ selections). Until it is, "
          "prefer flat/quarter-Kelly over full Kelly — over-betting a wrong edge is ruin.")

if __name__ == "__main__":
    main()
