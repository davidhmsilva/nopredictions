"""
mm_sim.py — two-sided market-making simulator on real Polymarket book + trade flow.

This is NOT the old maker_shadow test. That one rested a single DIRECTIONAL bid and
asked "did I get a better price than crossing" — a directional bet executed passively.
Here we quote BOTH sides, carry inventory, and the P&L is spread capture + rebates
MINUS adverse selection MINUS inventory mark-to-market.

Data:
  book   = pm_ticks (top-of-book + 5 levels, ~60s cadence)
  flow   = data-api /trades?market= (real taker prints, continuous)

Everything is normalised into YES-space, because on PM "selling YES" is expressed as
"buying NO". Without that conversion flow looks 91% one-directional and the whole
exercise is meaningless.

Conservative-by-design choices (each one costs us money in the sim, deliberately):
  * back-of-queue: we only fill the part of a taker print that EXCEEDS the depth
    already resting at our level. If $6k is ahead of us and a $500 order arrives,
    we get nothing.
  * quotes are refreshed only when a new book snapshot arrives (~60s). Between
    snapshots our quote is stale and can be picked off — that IS adverse selection
    and we want it in the number, not assumed away.
  * inventory is marked at the final observed mid, never at our own quote.
  * maker fee = 0 (PM charges takers only — verified on 87k fills, see pm_fees memory).
"""
from __future__ import annotations
import json, sys, collections, statistics as st, argparse

S = "/private/tmp/claude-501/-Users-davidsilva-Documents-agente/cda0ca3b-f979-48ab-8313-4b59ea579d17/scratchpad/"
TICK = 0.01


try:
    RESOLVED = {k: float(v) for k, v in json.load(open(S + "mm_resolved.json")).items()}
except Exception:
    RESOLVED = {}


def load():
    ticks = json.load(open(S + "mm_ticks.json"))
    trades = json.load(open(S + "mm_trades.json"))
    return ticks, trades


def build_books(ticks):
    """token -> sorted list of snapshots"""
    by = collections.defaultdict(list)
    for t in ticks:
        by[t["tok"]].append(t)
    for k in by:
        by[k].sort(key=lambda r: r["ts"])
    return by


def yes_space(trades, books):
    """
    Convert every taker print into YES-space for its market.
    Returns cid -> {yes_token, no_token, prints:[(ts, dir, price_yes, size_shares)]}
    dir = +1 taker BUYS yes (lifts our ask), -1 taker SELLS yes (hits our bid)
    """
    out = {}
    for cid, rows in trades.items():
        toks = {}
        for r in rows:
            if r.get("outcomeIndex") in (0, 1):
                toks[r["outcomeIndex"]] = r["asset"]
        if 0 not in toks:
            continue
        prints = []
        for r in rows:
            oi = r.get("outcomeIndex")
            if oi not in (0, 1):
                continue
            p, sz = r["price"], r["size"]
            if oi == 0:
                d = 1 if r["side"] == "BUY" else -1
                py = p
            else:
                d = -1 if r["side"] == "BUY" else 1
                py = 1.0 - p
            prints.append((r["timestamp"], d, py, sz))
        prints.sort()
        out[cid] = {"yes": toks[0], "no": toks.get(1), "prints": prints}
    return out


def level_size(levels, price, side):
    """shares resting exactly at `price` on `side`. levels = [[price,size],...]"""
    if not levels:
        return 0.0
    for lv in levels:
        try:
            lp, ls = float(lv[0]), float(lv[1])
        except Exception:
            continue
        if abs(lp - price) < 1e-9:
            return ls
    return 0.0


def snap_at(book, ts):
    """most recent snapshot at or before ts (book is time-sorted)"""
    lo, hi, best = 0, len(book) - 1, None
    while lo <= hi:
        m = (lo + hi) // 2
        if book[m]["ts"] <= ts:
            best = book[m]; lo = m + 1
        else:
            hi = m - 1
    return best


def simulate(cid, mk, books, quote_usd, max_inv_usd, mode, rebate_bps):
    """
    mode: 'join'    -> quote at the touch
          'improve' -> step inside by 1 tick whenever spread >= 2 ticks
    Returns dict of results, or None if unusable.
    """
    ytok = mk["yes"]
    book = books.get(ytok)
    if not book or len(book) < 20:
        return None
    prints = mk["prints"]
    if not prints:
        return None

    inv = 0.0            # shares of YES (can go negative = short YES)
    cash = 0.0
    bought = sold = 0.0  # shares
    bvol = svol = 0.0    # $ traded (for rebate + margin denominator)
    nfill = 0
    adverse = []         # (fill_price, mid_after) to measure adverse selection

    for ts, d, py, sz in prints:
        sn = snap_at(book, ts)
        if sn is None:
            continue
        bid, ask = sn["bid"], sn["ask"]
        if not (0.02 < bid < ask < 0.98):
            continue
        spread_ticks = round((ask - bid) / TICK)
        if spread_ticks <= 0:
            continue

        my_bid, my_ask = bid, ask
        if mode == "improve" and spread_ticks >= 2:
            my_bid = round(bid + TICK, 4)
            my_ask = round(ask - TICK, 4)
            if my_bid >= my_ask:
                my_bid, my_ask = bid, ask

        # queue ahead of us at our own level (0 if we improved to an empty level)
        ahead_b = 0.0 if my_bid > bid + 1e-9 else level_size(sn.get("bl"), bid, "b")
        ahead_a = 0.0 if my_ask < ask - 1e-9 else level_size(sn.get("al"), ask, "a")

        my_sz_b = quote_usd / max(my_bid, 0.01)
        my_sz_a = quote_usd / max(1 - my_ask, 0.01)

        if d < 0 and py <= my_bid + 1e-9:
            # taker sells YES into our bid
            if inv * my_bid < max_inv_usd:
                fill = min(my_sz_b, max(0.0, sz - ahead_b))
                if fill > 0:
                    inv += fill; cash -= fill * my_bid
                    bought += fill; bvol += fill * my_bid; nfill += 1
                    adverse.append((my_bid, +1, ts))
        elif d > 0 and py >= my_ask - 1e-9:
            # taker buys YES from our ask
            if -inv * (1 - my_ask) < max_inv_usd:
                fill = min(my_sz_a, max(0.0, sz - ahead_a))
                if fill > 0:
                    inv -= fill; cash += fill * my_ask
                    sold += fill; svol += fill * my_ask; nfill += 1
                    adverse.append((my_ask, -1, ts))

    if nfill < 5:
        return None
    last = book[-1]
    mid = (last["bid"] + last["ask"]) / 2
    # settle residual inventory at the real outcome when the market resolved,
    # otherwise mark at the last observed mid. Marking a match market at the
    # last mid pretends we could flatten at no cost, which we cannot.
    settle = RESOLVED.get(ytok)
    mark = settle if settle is not None else mid
    mtm = cash + inv * mark
    vol = bvol + svol
    rebate = vol * rebate_bps / 10000.0

    # --- decomposition: spread capture vs directional inventory ---
    # matched shares are the part we actually round-tripped; the residual is a
    # directional position we were left holding, which is NOT market making.
    matched = min(bought, sold)
    vwap_b = (bvol / bought) if bought else 0.0
    vwap_a = (svol / sold) if sold else 0.0
    spread_pnl = matched * (vwap_a - vwap_b)
    inv_pnl = mtm - spread_pnl

    # adverse selection: where did mid go after each fill?
    adv = []
    for fp, sign, ts in adverse:
        fut = snap_at(book, ts + 600)          # 10 minutes later
        if fut is None:
            continue
        fmid = (fut["bid"] + fut["ask"]) / 2
        adv.append(sign * (fmid - fp))         # +ve = mid moved in our favour
    return dict(cid=cid, fills=nfill, bought=bought, sold=sold, vol=vol,
                gross=mtm, net=mtm + rebate, rebate=rebate,
                spread_pnl=spread_pnl, inv_pnl=inv_pnl, matched=matched,
                inv_end=inv, inv_usd=inv * mark, settled=settle is not None,
                adv=st.mean(adv) if adv else None, nadv=len(adv),
                q=book[0].get("q"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="join", choices=["join", "improve"])
    ap.add_argument("--quote-usd", type=float, default=50.0)
    ap.add_argument("--max-inv-usd", type=float, default=500.0)
    ap.add_argument("--rebate-bps", type=float, default=30.0)
    a = ap.parse_args()

    ticks, trades = load()
    books = build_books(ticks)
    mks = yes_space(trades, books)

    res = []
    for cid, mk in mks.items():
        r = simulate(cid, mk, books, a.quote_usd, a.max_inv_usd, a.mode, a.rebate_bps)
        if r:
            res.append(r)
    if not res:
        print("no simulable markets"); return

    V = sum(r["vol"] for r in res)
    G = sum(r["gross"] for r in res)
    N = sum(r["net"] for r in res)
    R = sum(r["rebate"] for r in res)
    F = sum(r["fills"] for r in res)
    print(f"=== two-sided MM sim | mode={a.mode} quote=${a.quote_usd:.0f} "
          f"inv_cap=${a.max_inv_usd:.0f} rebate={a.rebate_bps:.0f}bps ===")
    print(f"markets simulated : {len(res)}")
    print(f"fills             : {F:,d}")
    print(f"volume traded     : ${V:,.0f}")
    print(f"gross P&L (spread - adverse - inventory) : ${G:>10,.2f}  = {G/V:+.3%} of volume")
    print(f"rebates @{a.rebate_bps:.0f}bps                              : ${R:>10,.2f}")
    print(f"NET P&L                                  : ${N:>10,.2f}  = {N/V:+.3%} of volume")
    inv = sum(abs(r["inv_usd"]) for r in res)
    print(f"residual |inventory| at end              : ${inv:,.0f}")

    SP = sum(r["spread_pnl"] for r in res)
    IP = sum(r["inv_pnl"] for r in res)
    M = sum(r["matched"] for r in res)
    B = sum(r["bought"] for r in res)
    Sd = sum(r["sold"] for r in res)
    print(f"\n--- DECOMPOSITION (this is the whole point) ---")
    print(f"  spread capture (round-tripped)  : ${SP:>10,.2f}  = {SP/V:+.3%} of volume")
    print(f"  directional inventory P&L       : ${IP:>10,.2f}  = {IP/V:+.3%} of volume")
    print(f"  -> inventory is {abs(IP)/(abs(SP)+abs(IP)):.0%} of the absolute P&L")
    print(f"  shares bought {B:,.0f} / sold {Sd:,.0f} / MATCHED {M:,.0f} "
          f"({M/max(B,Sd):.0%} of the larger side)")
    print(f"  markets settled at real outcome : {sum(1 for r in res if r['settled'])}/{len(res)}")
    advs = [r["adv"] for r in res if r["adv"] is not None]
    if advs:
        print(f"\nadverse selection (mid move 10min after fill, +ve = in our favour):")
        print(f"  mean {st.mean(advs)*100:+.2f}pp   median {st.median(advs)*100:+.2f}pp   "
              f"markets {len(advs)}")
    wins = sum(1 for r in res if r["net"] > 0)
    print(f"profitable markets: {wins}/{len(res)} = {wins/len(res):.0%}")
    print("\nper-market (top/bottom by net):")
    res.sort(key=lambda r: -r["net"])
    for r in res[:5] + res[-5:]:
        print(f"  net ${r['net']:>9,.2f} on ${r['vol']:>9,.0f} ({r['net']/r['vol']:+7.3%}) "
              f"fills={r['fills']:>4d} inv=${r['inv_usd']:>8,.0f}  {(r['q'] or '')[:40]}")


if __name__ == "__main__":
    main()


def bootstrap(res, B=20000, seed=13):
    import random
    random.seed(seed)
    out = []
    for _ in range(B):
        s = [res[random.randrange(len(res))] for _ in range(len(res))]
        v = sum(x["vol"] for x in s)
        if v > 0:
            out.append(sum(x["net"] for x in s) / v)
    out.sort()
    return out[int(.025 * len(out))], out[int(.975 * len(out))], sum(1 for x in out if x <= 0) / len(out)
