"""
grids.py — templates that turn one idea into hundreds of specs.

Each template crosses the dimensions a trader would actually vary — when, in what
state, at what price, on what book, with what live filter, and how to get out —
and drops the combinations that cannot exist (0 goals and a two-goal lead).
Adding a template is how the factory learns a new kind of strategy; the engine
does not care where a spec came from.
"""
from __future__ import annotations

from itertools import product

from .spec import normalize

INPLAY_BANDS = [(1.01, 1.5), (1.5, 2.2), (2.2, 4.0), (4.0, 15.0)]
GATES = [("loose", {"max_spread": 0.10, "min_depth_usd": 25}),
         ("tight", {"max_spread": 0.03, "min_depth_usd": 200})]
PRE = [("", []), ("pre<.50", [["pre_over25", "<", 0.50]]),
       ("pre.50-.60", [["pre_over25", "between", [0.50, 0.60]]]),
       ("pre≥.60", [["pre_over25", ">=", 0.60]])]


def _spec(template, universe, name_bits, where, price, exit=None, market=None, side=None):
    return {"template": template, "universe": universe, "market": market, "side": side,
            "name": " · ".join(b for b in name_bits if b), "where": where, "price": price,
            "exit": exit or {"type": "hold"}, "stake": {"type": "flat", "units": 1}}


def next_goal() -> list:
    out = []
    windows = [(20, 34), (35, 45), (46, 59), (60, 69), (70, 79), (80, 89)]
    states = [("0-0", 0, 0), ("1 goal", 1, 1), ("2 level", 2, 0), ("2 by2", 2, 2),
              ("3+ level", 3, 0), ("3+ by1", 3, 1), ("3+ by2+", 3, 2)]
    live = [("", []), ("SoT15≥2", [["shots_on_window", ">=", 2]]),
            ("xG15≥.4", [["xg_window", ">=", 0.4]]), ("red", [["reds_total", ">=", 1]])]
    for (a, b), (sn, g, d), (pn, pw), (lo, hi), (gn, gate), (ln, lw) in product(
            windows, states, PRE, INPLAY_BANDS, GATES, live):
        gw = [["goals_total", ">=" if g == 3 else "==", g]]
        dw = [["abs_diff", ">=" if d == 2 and g == 3 else "==", d]]
        where = [["minute", "between", [a, b]]] + gw + dw + pw + lw
        price = {"odds_min": lo, "odds_max": hi, **gate}
        bits = ["NG", f"{a}-{b}'", sn, pn, f"@{lo:g}-{hi:g}", gn, ln]
        out.append(_spec("next_goal", "soccer_inplay_next_goal", bits, where, price))
        if b + 10 <= 88:     # swing: in before the goal, out ten minutes later if it has not come
            out.append(_spec("next_goal_swing", "soccer_inplay_next_goal", bits + [f"out@{b + 10}'"],
                             where, price, {"type": "cash_out", "minute": b + 10}))
    return out


def ht_over05() -> list:
    out = []
    windows = [(15, 22), (23, 30), (31, 38), (39, 45)]
    bands = [(1.2, 1.6), (1.6, 2.2), (2.2, 3.5), (3.5, 8.0)]
    live = [("", []), ("press≥19", [["pressure_now", ">=", 19]]),
            ("SoT≥2", [["shots_on_total", ">=", 2]]), ("xG≥.4", [["xg_total", ">=", 0.4]])]
    for (a, b), (pn, pw), (lo, hi), (gn, gate), (ln, lw) in product(windows, PRE, bands, GATES, live):
        where = [["minute", "between", [a, b]]] + pw + lw
        price = {"odds_min": lo, "odds_max": hi, **gate}
        bits = ["HT0.5", f"{a}-{b}'", pn, f"@{lo:g}-{hi:g}", gn, ln]
        out.append(_spec("ht_over05", "soccer_inplay_ht_over05", bits, where, price))
        if b + 7 <= 44:
            out.append(_spec("ht_over05_swing", "soccer_inplay_ht_over05", bits + [f"out@{b + 7}'"],
                             where, price, {"type": "cash_out", "minute": b + 7}))
    return out


def fav_ht() -> list:
    out = []
    windows = [(15, 25), (26, 35), (36, 44)]
    favs = [("fav.50-.60", [["fav_prob", "between", [0.50, 0.60]]]),
            ("fav.60-.70", [["fav_prob", "between", [0.60, 0.70]]]),
            ("fav≥.70", [["fav_prob", ">=", 0.70]])]
    leads = [("level", [["fav_lead", "==", 0]]), ("fav behind", [["fav_lead", "<=", -1]]),
             ("fav ahead", [["fav_lead", ">=", 1]])]
    live = [("", []), ("dom≥20", [["dominance_now", ">=", 20]]), ("favP≥19", [["fav_pressure_now", ">=", 19]])]
    bands = [(1.05, 1.6), (1.6, 2.5), (2.5, 4.0), (4.0, 12.0)]
    for (a, b), (fn, fw), (lname, lw), (dn, dw), (lo, hi), (gn, gate) in product(
            windows, favs, leads, live, bands, GATES):
        where = [["minute", "between", [a, b]]] + fw + lw + dw
        bits = ["FAV-HT", f"{a}-{b}'", fn, lname, dn, f"@{lo:g}-{hi:g}", gn]
        out.append(_spec("fav_ht", "soccer_inplay_fav_ht", bits, where,
                         {"odds_min": lo, "odds_max": hi, **gate}))
    return out


def settled() -> list:
    out = []
    phases = ["post_whistle", "halftime", "in_match"]
    rules = [None, "exact_score_no", "exact_score", "ft_total_over", "ft_total_under",
             "h1_total_over", "ht_lead", "ft_win", "ht_draw", "btts_yes"]
    bands = [(1.005, 1.053), (1.053, 1.25), (1.25, 2.0)]      # asks 0.95-0.995 / 0.80-0.95 / 0.50-0.80
    depths = [("", 10), ("$100+", 100)]
    for ph, rule, (lo, hi), (dn, dep) in product(phases, rules, bands, depths):
        where = [["phase", "==", ph]] + ([["rule", "==", rule]] if rule else [])
        bits = ["SETTLED", ph, rule or "any rule", f"@{lo:g}-{hi:g}", dn]
        out.append(_spec("settled", "soccer_settled", bits, where,
                         {"odds_min": lo, "odds_max": hi, "min_depth_usd": dep}))
    return out


def _soccer_prematch(universe: str, with_move: bool) -> list:
    out = []
    tiers = [("", []), ("tier1", [["tier", "==", 1]]), ("tier2+", [["tier", ">=", 2]])]
    filters = [("", []), ("form+6", [["form_diff", ">=", 6]]), ("form-6", [["form_diff", "<=", -6]]),
               ("rest+3", [["rest_diff", ">=", 3]]), ("rest-3", [["rest_diff", "<=", -3]]),
               ("tg5≥3.2", [["tg5_sum", ">=", 3.2]]), ("tg5≤2.2", [["tg5_sum", "<=", 2.2]])]
    if with_move:     # momentum: the sharp price moved open → close (known at the close)
        filters += [("steam≥2pp", [["move", ">=", 0.02]]), ("drift≥2pp", [["move", "<=", -0.02]])]
    bands_1x2 = [(1.2, 1.6), (1.6, 2.2), (2.2, 3.2), (3.2, 6.0), (6.0, 15.0)]
    bands_ou = [(1.4, 1.8), (1.8, 2.2), (2.2, 3.0)]
    favs = [("", []), ("fav", [["is_fav", "==", 1]]), ("dog", [["is_fav", "==", 0]])]
    for side in ("home", "away", "draw"):
        for (tn, tw), (fvn, fvw), (lo, hi), (fn, fw) in product(
                tiers, favs if side != "draw" else [("", [])], bands_1x2, filters):
            out.append(_spec(f"{universe}_1x2", universe, [universe.split("_")[-1], side, tn, fvn,
                                                           f"@{lo:g}-{hi:g}", fn],
                             tw + fvw + fw, {"odds_min": lo, "odds_max": hi}, market="1x2", side=side))
    for side in ("over", "under"):
        for (tn, tw), (lo, hi), (fn, fw) in product(tiers, bands_ou, filters):
            out.append(_spec(f"{universe}_ou25", universe, [universe.split("_")[-1], f"{side} 2.5", tn,
                                                            f"@{lo:g}-{hi:g}", fn],
                             tw + fw, {"odds_min": lo, "odds_max": hi}, market="ou25", side=side))
    return out


def soccer_prematch_close() -> list:
    return _soccer_prematch("soccer_prematch_close", with_move=True)


def soccer_prematch_open() -> list:
    return _soccer_prematch("soccer_prematch_open", with_move=False)


def nfl_prematch() -> list:
    out = []
    margins = [("fav 0-3.5", [["exp_margin", "between", [0.01, 3.5]]]),
               ("fav 4-7.5", [["exp_margin", "between", [3.51, 7.5]]]),
               ("fav 8+", [["exp_margin", ">", 7.5]]),
               ("dog 0-3.5", [["exp_margin", "between", [-3.5, -0.01]]]),
               ("dog 4-7.5", [["exp_margin", "between", [-7.5, -3.51]]]),
               ("dog 8+", [["exp_margin", "<", -7.5]])]
    filters = [("", []), ("div", [["div_game", "==", 1]]), ("prime", [["primetime", "==", 1]]),
               ("rest+3", [["rest_diff", ">=", 3]]), ("rest-3", [["rest_diff", "<=", -3]]),
               ("playoff", [["playoff", "==", 1]]), ("wind≥15", [["wind", ">=", 15]]),
               ("late season", [["week", ">=", 13]])]
    for market, side, (mn, mw), (fn, fw) in product(("ml", "spread"), ("home", "away"), margins, filters):
        out.append(_spec(f"nfl_{market}", "nfl_prematch", ["NFL", market, side, mn, fn],
                         mw + fw, {}, market=market, side=side))
    totals = [("", []), ("tot≤40", [["total_line", "<=", 40]]),
              ("tot40-47", [["total_line", "between", [40.5, 47]]]), ("tot≥47.5", [["total_line", ">=", 47.5]])]
    tfilters = filters + [("dome", [["roof", "==", "dome"]]), ("outdoor", [["roof", "==", "outdoors"]])]
    for side, (tn, tw), (fn, fw) in product(("over", "under"), totals, tfilters):
        out.append(_spec("nfl_total", "nfl_prematch", ["NFL total", side, tn, fn], tw + fw, {},
                         market="total", side=side))
    return out


TEMPLATES = {
    "soccer_inplay_next_goal": next_goal,
    "soccer_inplay_ht_over05": ht_over05,
    "soccer_inplay_fav_ht": fav_ht,
    "soccer_settled": settled,
    "soccer_prematch_close": soccer_prematch_close,
    "soccer_prematch_open": soccer_prematch_open,
    "nfl_prematch": nfl_prematch,
}


def all_specs(universes=None) -> list:
    out, seen = [], set()
    from .spec import spec_hash
    for u, fn in TEMPLATES.items():
        if universes and u not in universes:
            continue
        for raw in fn():
            s = normalize(raw)
            h = spec_hash(s)
            if h not in seen:
                seen.add(h)
                out.append(s)
    return out
