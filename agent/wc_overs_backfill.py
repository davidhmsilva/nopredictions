"""
wc_overs_backfill — reconstruct in-play over_2.5 price curves for past WC matches.

For every finished WC match we have a Polymarket "O/U 2.5" market for:
  1. Fetch the Over token id + game_start_time from CLOB /markets/{condition_id}
  2. Fetch /prices-history (interval=max) for that token
  3. Fetch the fixture from api-football (date + team name match)
  4. Get /fixtures/events → build a minute→score timeline
  5. Walk the price snapshots; tag each with elapsed minute + score
  6. Report: min price while score still 0-0 or one goal, from minute 30 / HT / 60

Reads: pm_markets, matches, teams, team_aliases, ENV(FOOTBALL_API_KEY)
Writes: nothing (read-only analysis).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "ingest" / ".env")

sys.path.insert(0, str(ROOT / "agent"))

DATABASE_URL = os.environ["DATABASE_URL"]
CLOB = os.getenv("POLYMARKET_CLOB_API", "https://clob.polymarket.com").rstrip("/")
GAMMA = os.getenv("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com").rstrip("/")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")

# DC model + sim engine for in-play model probabilities
from dixon_coles import DixonColesModel  # noqa: E402
from sim.simulator import simulate, SimConfig  # noqa: E402
from sim.state import MatchState  # noqa: E402
from sim.pricer import price_markets  # noqa: E402
import sim_scanner as ss  # noqa: E402  (for _norm / PARAMS_PATH)

_DC_MODEL = None
_NORM_IDX: dict = {}


def _load_model():
    global _DC_MODEL, _NORM_IDX
    if _DC_MODEL is None:
        _DC_MODEL = DixonColesModel.load(ss.PARAMS_PATH)
        _NORM_IDX = {ss._norm(t): i for i, t in enumerate(_DC_MODEL.teams)}
    return _DC_MODEL


def _resolve_team(name: str) -> Optional[str]:
    """Return canonical model team name for a PM team string."""
    if not _DC_MODEL:
        return None
    n = ss._norm(name)
    if n in _NORM_IDX:
        return _DC_MODEL.teams[_NORM_IDX[n]]
    # try aliases
    for n2 in _candidates(name):
        n2n = ss._norm(n2)
        if n2n in _NORM_IDX:
            return _DC_MODEL.teams[_NORM_IDX[n2n]]
    return None


def model_over25_prob(home: str, away: str, minute: int, score_total: int) -> Optional[float]:
    """Run the in-play sim at this state and return model_prob(over_2.5)."""
    model = _load_model()
    h = _resolve_team(home)
    a = _resolve_team(away)
    if not h or not a:
        return None
    try:
        pred = model.predict(h, a)
        lh, la = pred["lambda_home"], pred["lambda_away"]
    except Exception:
        return None
    # split current score between home/away naively (doesn't matter for over_2.5)
    hs = score_total
    as_ = 0
    initial = MatchState.at(n_sims=4000, minute=minute, home=hs, away=as_, red_h=0, red_a=0)
    sim_res = simulate(lh, la, initial_state=initial, config=SimConfig(n_sims=4000, seed=42))
    return float(price_markets(sim_res).get("over_2_5", 0.0))

WC_TEAMS = {
    "Argentina", "Brazil", "Mexico", "Canada", "Qatar", "Switzerland",
    "Bosnia and Herzegovina", "Croatia", "Panama", "Portugal", "South Africa",
    "Czechia", "Korea Republic", "Senegal", "Cameroon", "Morocco",
    "Saudi Arabia", "Jordan", "Australia", "IR Iran", "Iran", "Japan",
    "Egypt", "Ghana", "Algeria", "Tunisia", "Ecuador", "Chile", "Peru",
    "Uruguay", "Colombia", "United States", "USA", "France", "Germany",
    "Spain", "England", "Italy", "Belgium", "Netherlands", "Denmark",
    "Sweden", "Norway", "Poland", "Serbia", "DR Congo", "Ivory Coast",
    "Nigeria", "Costa Rica", "Honduras", "Jamaica", "New Zealand", "Wales",
    "Scotland", "Turkey", "Greece", "Austria", "Hungary", "Romania",
    "Ukraine", "Mali", "Iraq", "UAE", "Uzbekistan", "Bolivia", "Venezuela",
    "Paraguay", "Slovakia", "Slovenia", "Cape Verde", "Haiti",
}


def clob_market(condition_id: str) -> Optional[dict]:
    try:
        r = requests.get(f"{CLOB}/markets/{condition_id}", timeout=15)
        if r.ok:
            return r.json()
    except requests.RequestException:
        return None
    return None


def over_token(mkt: dict) -> Optional[str]:
    for tok in mkt.get("tokens", []):
        if (tok.get("outcome") or "").lower() == "over":
            return str(tok.get("token_id"))
    return None


def prices_history(token_id: str, start_ts: int | None = None, end_ts: int | None = None) -> list[tuple[int, float]]:
    params: dict = {"market": token_id, "fidelity": 1}
    if start_ts is not None and end_ts is not None:
        params["startTs"] = start_ts
        params["endTs"] = end_ts
    else:
        params["interval"] = "max"
    try:
        r = requests.get(
            f"{CLOB}/prices-history",
            params=params,
            timeout=20,
        )
        if not r.ok:
            return []
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for pt in data.get("history") or []:
        t, p = pt.get("t"), pt.get("p")
        if t is None or p is None:
            continue
        try:
            out.append((int(t), float(p)))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


NAME_ALIASES = {
    "korea republic": ["south korea", "korea"],
    "türkiye": ["turkey", "turkiye"],
    "cabo verde": ["cape verde"],
    "côte d'ivoire": ["ivory coast", "cote d'ivoire", "cote divoire"],
    "ir iran": ["iran"],
    "bosnia-herzegovina": ["bosnia and herzegovina", "bosnia"],
    "curaçao": ["curacao"],
    "dr congo": ["congo dr", "congo"],
}


def _norm(s: str) -> str:
    return (s or "").lower().replace(".", "").replace("-", " ").strip()


def _candidates(name: str) -> list[str]:
    n = _norm(name)
    return [n] + NAME_ALIASES.get(n.replace(" ", "-"), []) + NAME_ALIASES.get(n, [])


def af_find_fixture(date_str: str, home: str, away: str) -> Optional[int]:
    """api-football fixture id by date + team name fuzzy match with aliases."""
    if not FOOTBALL_API_KEY:
        return None
    try:
        r = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"date": date_str},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except requests.RequestException:
        return None

    h_cands = _candidates(home)
    a_cands = _candidates(away)

    def matches(api_name: str, cands: list[str]) -> bool:
        n = _norm(api_name)
        return any(n == c or c in n or n in c for c in cands)

    for fix in data.get("response") or []:
        teams = fix.get("teams") or {}
        h = teams.get("home", {}).get("name", "")
        a = teams.get("away", {}).get("name", "")
        if (matches(h, h_cands) and matches(a, a_cands)) or \
           (matches(h, a_cands) and matches(a, h_cands)):
            return fix.get("fixture", {}).get("id")
    return None


def af_goal_timeline(fixture_id: int) -> list[tuple[int, str]]:
    """Return [(minute, scoring_team_name)] — sorted by minute."""
    if not FOOTBALL_API_KEY:
        return []
    try:
        r = requests.get(
            "https://v3.football.api-sports.io/fixtures/events",
            params={"fixture": fixture_id},
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except requests.RequestException:
        return []
    out = []
    for ev in data.get("response") or []:
        if ev.get("type") != "Goal":
            continue
        detail = (ev.get("detail") or "").lower()
        if "missed penalty" in detail:
            continue
        t = ev.get("time") or {}
        minute = (t.get("elapsed") or 0) + (t.get("extra") or 0)
        team = (ev.get("team") or {}).get("name", "")
        out.append((minute, team))
    out.sort()
    return out


def score_at_minute(timeline: list[tuple[int, str]], minute: int, home_name: str) -> tuple[int, int]:
    h = a = 0
    for m, team in timeline:
        if m > minute:
            break
        if team == home_name:
            h += 1
        else:
            a += 1
    return h, a


def list_wc_events() -> list[dict]:
    """Fetch WC fixture events from Gamma. Returns events with slug+title."""
    out: list[dict] = []
    offset = 0
    while True:
        try:
            r = requests.get(
                f"{GAMMA}/events",
                params={
                    "tag_slug": "fifa-world-cup",
                    "limit": 100,
                    "offset": offset,
                    "end_date_min": "2026-06-05T00:00:00Z",
                    "end_date_max": "2026-06-19T23:59:59Z",
                    "closed": "true",
                },
                timeout=15,
            )
            if not r.ok:
                break
            batch = r.json() or []
        except requests.RequestException:
            break
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    # Keep only main fixture events: title is "X vs. Y" (no " - Subtype" suffix)
    fixtures = []
    seen_slugs: set[str] = set()
    for e in out:
        slug = e.get("slug", "")
        title = e.get("title", "")
        if not slug.startswith("fifwc-"):
            continue
        if " - " in title:  # "X vs Y - More Markets", "- First Team to Score" etc.
            continue
        if " vs. " not in title:
            continue
        # canonical fixture slug has exactly 5 parts: fifwc, h3, a3, YYYY, MM, DD
        parts = slug.split("-")
        if len(parts) != 6:
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        fixtures.append(e)
    return fixtures


def gamma_market(slug: str) -> Optional[dict]:
    try:
        r = requests.get(f"{GAMMA}/markets", params={"slug": slug, "closed": "true"}, timeout=15)
        if r.ok:
            arr = r.json()
            if arr:
                return arr[0]
    except requests.RequestException:
        pass
    return None


def parse_match(title: str) -> Optional[tuple[str, str]]:
    """'Netherlands vs. Japan: O/U 2.5' → ('Netherlands','Japan')."""
    if ": " not in title:
        return None
    left = title.split(": ", 1)[0]
    sep = " vs. " if " vs. " in left else " vs " if " vs " in left else None
    if not sep:
        return None
    h, a = left.split(sep, 1)
    return h.strip(), a.strip()


def is_wc_pair(h: str, a: str) -> bool:
    return h in WC_TEAMS and a in WC_TEAMS


def run() -> None:
    events = list_wc_events()
    print(f"WC fixture events found: {len(events)}", file=sys.stderr)
    rows: list[dict] = []
    seen: set[str] = set()

    for ev in events:
        slug = ev.get("slug", "")
        title = ev.get("title", "")
        if " vs. " not in title:
            continue
        home, away = title.split(" vs. ", 1)
        home, away = home.strip(), away.strip()
        if slug in seen:
            continue
        seen.add(slug)

        ou_slug = f"{slug}-total-2pt5"
        gm = gamma_market(ou_slug)
        if not gm:
            print(f"  skip {home} v {away}: no O/U 2.5 market", file=sys.stderr)
            continue
        cond_id = gm.get("conditionId")
        cm = clob_market(cond_id) if cond_id else None
        if not cm:
            print(f"  skip {home} v {away}: CLOB miss", file=sys.stderr)
            continue
        gst = cm.get("game_start_time") or gm.get("gameStartTime") or gm.get("endDate")
        if not gst:
            print(f"  skip {home} v {away}: no game_start_time", file=sys.stderr)
            continue
        try:
            kickoff = datetime.fromisoformat(gst.replace("Z", "+00:00"))
        except ValueError:
            continue
        ko_ts = int(kickoff.timestamp())

        tok = over_token(cm)
        if not tok:
            continue

        # window: 30 min before kickoff to 130 min after, ~1 min fidelity
        hist = prices_history(tok, start_ts=ko_ts - 30 * 60, end_ts=ko_ts + 130 * 60)
        if not hist:
            print(f"  skip {home} v {away}: no price history", file=sys.stderr)
            continue

        # Find fixture in api-football (by kickoff date local UTC date)
        date_for_af = kickoff.date().isoformat()
        fid = af_find_fixture(date_for_af, home, away)
        if fid is None:
            # try day after (late kickoffs cross UTC)
            from datetime import timedelta
            fid = af_find_fixture((kickoff + timedelta(days=1)).date().isoformat(), home, away)
        timeline = af_goal_timeline(fid) if fid else []
        final_h = final_a = None
        if timeline:
            # final score = at minute 999
            final_h, final_a = score_at_minute(timeline, 999, home)

        # Walk history. Keep a trail of (minute, price, score_total) for backtest.
        trail: list[tuple[int, float, int]] = []
        per_state = {
            "min_at_0_0": None,
            "min_at_0_0_from30": None,
            "min_le_1g": None,
            "min_le_1g_from30": None,
            "min_le_1g_from45": None,
            "min_le_1g_from60": None,
            "n_snaps_inplay": 0,
        }
        for ts, price in hist:
            elapsed_s = ts - ko_ts
            if elapsed_s < 0 or elapsed_s > 130 * 60:
                continue
            minute = elapsed_s // 60
            if minute > 100:
                continue
            per_state["n_snaps_inplay"] += 1
            if not timeline:
                continue
            h, a = score_at_minute(timeline, int(minute), home)
            total = h + a
            trail.append((int(minute), float(price), total))

            def bump(key, val):
                cur = per_state[key]
                if cur is None or val < cur:
                    per_state[key] = val

            if total == 0:
                bump("min_at_0_0", price)
                if minute >= 30:
                    bump("min_at_0_0_from30", price)
            if total <= 1:
                bump("min_le_1g", price)
                if minute >= 30:
                    bump("min_le_1g_from30", price)
                if minute >= 45:
                    bump("min_le_1g_from45", price)
                if minute >= 60:
                    bump("min_le_1g_from60", price)

        if final_h is None or final_a is None:
            final_str = "—"
            over_final = "?"
        else:
            final_str = f"{final_h}-{final_a}"
            over_final = "YES" if (final_h + final_a) >= 3 else "no"

        rows.append({
            "home": home, "away": away, "date": date_for_af,
            "fid": fid, "n_inplay": per_state["n_snaps_inplay"],
            **{k: v for k, v in per_state.items() if k.startswith("min_")},
            "final": final_str, "over_final": over_final,
            "trail": trail,
            "final_total": (final_h + final_a) if (final_h is not None and final_a is not None) else None,
        })
        # be polite
        time.sleep(0.25)

    # Sort by best opportunity: lowest price while ≤1 goal from minute 30
    def sortkey(r):
        v = r["min_le_1g_from30"]
        return v if v is not None else 99

    rows.sort(key=sortkey)

    print()
    print("Min IN-PLAY pm_yes for OVER 2.5 while score still 0-0 or 1 goal — WC")
    print(f"{'date':11} {'match':46} {'@0-0':>6} {'@0-0/30+':>9} {'≤1g':>5} "
          f"{'≤1g/30+':>8} {'≤1g/HT+':>8} {'≤1g/60+':>8} {'final':>7} {'over?':>5} {'n':>5}")
    print("-" * 130)
    def fmt(v): return f"{v:.3f}" if v is not None else "  —  "
    for r in rows:
        m = f"{r['home']} v {r['away']}"[:46]
        print(f"{r['date']:11} {m:46} {fmt(r['min_at_0_0']):>6} {fmt(r['min_at_0_0_from30']):>9} "
              f"{fmt(r['min_le_1g']):>5} {fmt(r['min_le_1g_from30']):>8} {fmt(r['min_le_1g_from45']):>8} "
              f"{fmt(r['min_le_1g_from60']):>8} {r['final']:>7} {r['over_final']:>5} {r['n_inplay']:>5}")

    # ── Backtest ──────────────────────────────────────────────────────────────
    _load_model()
    print()
    print("BACKTEST — entry rule: first time pm_yes ≤ T with score ≤1 goal and "
          "30 ≤ minute ≤ 65 (NO model filter)")
    print(f"{'thresh':>7} {'n':>4} {'W':>4} {'L':>4} {'stake':>7} {'pnl':>8} "
          f"{'yield':>8} {'avg_entry':>10}")
    for T in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45):
        bets = []  # (price, won)
        for r in rows:
            if r["final_total"] is None:
                continue
            for minute, price, total in r["trail"]:
                if minute < 30 or minute > 65:
                    continue
                if total > 1:
                    continue
                if price <= T:
                    won = r["final_total"] >= 3
                    bets.append((price, won))
                    break
        if not bets:
            continue
        stake = float(len(bets))
        pnl = sum((1.0 / p - 1.0) if w else -1.0 for p, w in bets)
        wins = sum(1 for _, w in bets if w)
        avg_entry = sum(p for p, _ in bets) / len(bets)
        y = pnl / stake * 100
        print(f"  {T:>5.2f}  {len(bets):>4} {wins:>4} {len(bets)-wins:>4} "
              f"{stake:>6.1f}u {pnl:>+7.2f}u {y:>+7.1f}% {avg_entry:>9.3f}")

    # Now WITH model filter (model_prob - pm_yes >= MIN_EDGE)
    for MIN_EDGE in (0.08, 0.12, 0.15):
        print()
        print(f"WITH model filter: model_prob(over_2.5) - pm_yes >= {MIN_EDGE*100:.0f}pp")
        print(f"{'thresh':>7} {'n':>4} {'W':>4} {'L':>4} {'stake':>7} {'pnl':>8} "
              f"{'yield':>8} {'avg_entry':>10} {'avg_model':>10}")
        for T in (0.20, 0.25, 0.30, 0.35, 0.40):
            bets = []
            for r in rows:
                if r["final_total"] is None:
                    continue
                for minute, price, total in r["trail"]:
                    if minute < 30 or minute > 65 or total > 1 or price > T:
                        continue
                    mp = model_over25_prob(r["home"], r["away"], minute, total)
                    if mp is None or (mp - price) < MIN_EDGE:
                        continue
                    bets.append((price, mp, r["final_total"] >= 3))
                    break
            if not bets:
                continue
            stake = float(len(bets))
            pnl = sum((1.0 / p - 1.0) if w else -1.0 for p, _, w in bets)
            wins = sum(1 for _, _, w in bets if w)
            ae = sum(p for p, _, _ in bets) / len(bets)
            am = sum(m for _, m, _ in bets) / len(bets)
            y = pnl / stake * 100
            print(f"  {T:>5.2f}  {len(bets):>4} {wins:>4} {len(bets)-wins:>4} "
                  f"{stake:>6.1f}u {pnl:>+7.2f}u {y:>+7.1f}% {ae:>9.3f} {am:>9.3f}")

    # And the "perfect-timing" optimistic version: enter at the MIN price reached
    # in the eligible window. Only useful as upper bound.
    print()
    print("BEST-CASE (perfect timing, entry at min price in window 30–65 with ≤1g):")
    for T in (0.20, 0.25, 0.30, 0.35):
        bets = []
        for r in rows:
            if r["final_total"] is None:
                continue
            # min price in eligible window
            elig = [(m, p) for (m, p, t) in r["trail"] if 30 <= m <= 65 and t <= 1]
            if not elig:
                continue
            best_p = min(p for _, p in elig)
            if best_p <= T:
                bets.append((best_p, r["final_total"] >= 3))
        if not bets:
            continue
        stake = float(len(bets))
        pnl = sum((1.0 / p - 1.0) if w else -1.0 for p, w in bets)
        wins = sum(1 for _, w in bets if w)
        avg_entry = sum(p for p, _ in bets) / len(bets)
        y = pnl / stake * 100
        print(f"  {T:>5.2f}  {len(bets):>4} {wins:>4} {len(bets)-wins:>4} "
              f"{stake:>6.1f}u {pnl:>+7.2f}u {y:>+7.1f}% {avg_entry:>9.3f}")


if __name__ == "__main__":
    run()
