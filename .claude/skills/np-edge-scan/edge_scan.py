#!/usr/bin/env python3
"""
np-edge-scan — sharp-anchored edge scan of any Polymarket board.

For each PM match in a board (tag), compare the PM price against our DC model
and against the sharp line (Pinnacle/Betfair via The Odds API), then run the
edge_engine consensus gate (fair = min(model, sharp); edge = fair − PM ask).
REAL edge = where PM diverges and DC *and* sharp agree. Model delusions (DC
disagrees, sharp ≈ PM) are reported separately and never count as edge.

Usage:
  python edge_scan.py                                  # World Cup (default)
  python edge_scan.py --pm-tag epl --odds-sport soccer_epl
  python edge_scan.py --pm-tag champions-league --odds-sport soccer_uefa_champs_league
  python edge_scan.py --pm-tag <slug> --odds-sport ""  # model-only (no sharp)

Find the PM tag in the polymarket.com URL (/sports/<tag>/...). Find the Odds API
sport key at https://api.the-odds-api.com/v4/sports?apiKey=...  (the /sports call
is free). No settled outcomes are needed — this finds CANDIDATE edges to watch.
"""
from __future__ import annotations
import argparse, os, re, sys, json, unicodedata
import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
AGENT = os.path.join(REPO, "agent")
sys.path.insert(0, AGENT); sys.path.insert(0, os.path.join(AGENT, "tools"))
load_dotenv(os.path.join(REPO, "ingest", ".env"))

from dixon_coles import DixonColesModel          # noqa: E402
from dc_scanner import _norm, _find_team, _classify_market, DC_KEY_MAP  # noqa: E402
from edge_engine import compute_edge             # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
try:
    from dc_scanner import PARAMS_PATH
except Exception:
    PARAMS_PATH = os.path.join(AGENT, "dc_model_params.json")

# Convenience: common PM tag -> The Odds API sport key. Extend as needed.
TAG_TO_SPORT = {
    "fifa-world-cup": "soccer_fifa_world_cup", "world-cup": "soccer_fifa_world_cup",
    "epl": "soccer_epl", "premier-league": "soccer_epl",
    "la-liga": "soccer_spain_la_liga", "serie-a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga", "ligue-1": "soccer_france_ligue_one",
    "champions-league": "soccer_uefa_champs_league",
}
ALIAS = {
 "korea republic":"south korea","turkiye":"turkey","cote divoire":"ivory coast",
 "cote d ivoire":"ivory coast","czechia":"czech republic","cabo verde":"cape verde",
 "usa":"united states","bosnia and herzegovina":"bosnia","dr congo":"congo dr",
}
def cn(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii","ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]"," ", s); s = re.sub(r"\s+"," ", s).strip()
    return ALIAS.get(s, s)

def fetch_pm(tags):
    evs=[]
    for slug in tags:
        try:
            evs += requests.get(f"{GAMMA}/events", params={"tag_slug":slug,"closed":"false","limit":300}, timeout=30).json()
        except Exception as e:
            print(f"  (warn: PM fetch for tag '{slug}' failed: {e})")
    seen={}
    for e in evs:
        if ' vs' in (e.get('title') or '').lower(): seen[e.get('id')] = e
    return list(seen.values())

def fetch_sharp(sport):
    if not sport or not ODDS_KEY: return {}, None, 0
    try:
        r = requests.get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
            params={"apiKey":ODDS_KEY,"regions":"eu,uk","markets":"h2h","oddsFormat":"decimal",
                    "bookmakers":"pinnacle,betfair_ex_eu,betfair_ex_uk"}, timeout=30)
    except Exception as e:
        print(f"  (warn: sharp fetch failed: {e})"); return {}, None, 0
    rem=r.headers.get('x-requests-remaining'); data=r.json() if r.status_code==200 else []
    if r.status_code!=200: print(f"  (warn: Odds API {r.status_code}: {str(data)[:120]})")
    out={}
    for g in (data if isinstance(data,list) else []):
        ht,at=cn(g.get('home_team','')),cn(g.get('away_team',''))
        bk=next((b for b in g.get('bookmakers',[]) if b['key']=='pinnacle'),None) \
           or next((b for b in g.get('bookmakers',[]) if b['key'].startswith('betfair')),None)
        if not bk: continue
        h2h=next((m for m in bk['markets'] if m['key']=='h2h'),None)
        if not h2h: continue
        pr={}
        for o in h2h['outcomes']:
            pr['draw' if o['name'].strip().lower()=='draw' else cn(o['name'])]=1.0/o['price']
        s=sum(pr.values())
        if s>0: out[frozenset({ht,at})]={'probs':{k:v/s for k,v in pr.items()}}
    return out, rem, (len(data) if isinstance(data,list) else 0)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pm-tag", default="fifa-world-cup,world-cup", help="PM tag slug(s), comma-separated")
    ap.add_argument("--odds-sport", default=None, help="The Odds API sport key ('' to skip sharp)")
    ap.add_argument("--min-raw-pp", type=float, default=2.0, help="min raw edge to list (default 2pp)")
    args=ap.parse_args()
    tags=[t.strip() for t in args.pm_tag.split(",") if t.strip()]
    sport=args.odds_sport
    if sport is None:
        sport=next((TAG_TO_SPORT[t] for t in tags if t in TAG_TO_SPORT), "")
        if not sport: print(f"  (no sharp sport mapped for {tags}; model-only. Pass --odds-sport to enable.)")

    model=DixonColesModel.load(PARAMS_PATH)
    norm_idx={_norm(t):i for i,t in enumerate(model.teams)}
    games=fetch_pm(tags); sharp,rem,nsharp=fetch_sharp(sport)
    print(f"PM games: {len(games)} | sharp lines: {nsharp} | OddsAPI quota left: {rem}\n")

    rows=[]; n_dc=0; n_sharp=0; delus=0
    for e in games:
        parts=re.split(r'\s+vs\.?\s+', e.get('title',''), maxsplit=1)
        if len(parts)!=2: continue
        homePM,awayPM=parts[0].strip(),parts[1].strip()
        hi,ai=_find_team(homePM,norm_idx),_find_team(awayPM,norm_idx)
        if hi is None or ai is None: continue
        n_dc+=1
        pred=model.predict(model.teams[hi],model.teams[ai])
        pm={}
        for m in (e.get('markets') or []):
            if not m.get('active') or m.get('closed'): continue
            raw=m.get('outcomePrices')
            if not raw: continue
            prices=json.loads(raw) if isinstance(raw,str) else raw
            ok=_classify_market(m.get('question',''),homePM,awayPM)
            if ok in ('home','draw','away'): pm[ok]={'yes':float(prices[0]),'ask':m.get('bestAsk')}
        sp=sharp.get(frozenset({cn(homePM),cn(awayPM)}))
        if sp: n_sharp+=1
        for oc in ('home','draw','away'):
            if oc not in pm: continue
            dcp=pred.get(DC_KEY_MAP[oc])
            if dcp is None: continue
            xp=pm[oc]['ask'] or pm[oc]['yes']
            spp=sp['probs'].get({'home':cn(homePM),'away':cn(awayPM),'draw':'draw'}[oc]) if sp else None
            res=compute_edge(model_prob=dcp, exec_price=float(xp), sharp_prob=spp)
            if spp is not None and (dcp-xp)*100>=5 and (spp-xp)*100<2: delus+=1
            rows.append({'g':f"{homePM} v {awayPM}",'oc':oc,'pm':xp,'dc':dcp,'sp':spp,
                         'raw':res.edge_raw_pp,'adj':res.edge_pp,'ok':res.bet_ok,'conf':res.confidence})

    print(f"matched to DC: {n_dc}/{len(games)} | matched to sharp: {n_sharp}/{n_dc}\n")
    sv=sorted([r for r in rows if r['conf']=='sharp'], key=lambda r:r['adj'], reverse=True)
    print("="*84); print("SHARP-VALIDATED candidates (fair = min(DC,sharp) − PM ask, after haircut)"); print("="*84)
    print(f"{'game':30}{'pick':6}{'PMask':>7}{'DC':>7}{'sharp':>7}{'rawE':>7}{'adjE':>7}  verdict")
    shown=[r for r in sv if r['raw']>=args.min_raw_pp][:20]
    if not shown: print("  (no sharp-validated outcome above threshold — PM is efficient here)")
    for r in shown:
        print(f"{r['g'][:29]:30}{r['oc']:6}{r['pm']*100:>6.1f}%{r['dc']*100:>6.1f}%"
              f"{(r['sp']*100 if r['sp'] else 0):>6.1f}%{r['raw']:>+6.1f}{r['adj']:>+6.1f}  {'*** BET' if r['ok'] else 'skip'}")
    print(f"\n  sharp-validated BET signals: {len([r for r in sv if r['ok']])}")
    print(f"  model-only BET signals (NO sharp to validate — suspect): {len([r for r in rows if r['conf']=='model' and r['ok']])}")
    print(f"  model delusions (DC edge≥5pp but sharp says price fair): {delus}")

if __name__=="__main__":
    main()
