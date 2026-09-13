"""
factory_cli.py — the strategy factory from the command line.

    python factory_cli.py universes                    # what can be traded, and on which features
    python factory_cli.py build [--refresh]            # load + cache every tape
    python factory_cli.py grid [--universe U ...] [--register] [--refresh]
    python factory_cli.py backtest --spec '{...}'      # one spec (JSON or a file path), full report
    python factory_cli.py leaderboard [--limit 30]
    python factory_cli.py run [--dry-run]              # paper entries off the live tape (cron, 1/min)
    python factory_cli.py settle                       # settle, forward stats, promotion (cron)

A grid writes every spec it tested to reports/factory_grid_<date>.csv and
registers only the ones that passed (FDR on train, positive on the held-out test).
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from factory import engine, grids, registry, runner          # noqa: E402
from factory.spec import SpecError, normalize, spec_hash      # noqa: E402
from factory.universes import UNIVERSES, load_cached          # noqa: E402

log = logging.getLogger("factory")
REPORTS = os.path.join(HERE, "..", "reports")


def _pct(x, nd=1):
    return "   —  " if x is None else f"{100 * x:+.{nd}f}%"


def _frames(conn, names, refresh=False):
    out = {}
    for n in names:
        U = UNIVERSES[n]
        t = time.time()
        df = load_cached(conn, U, refresh=refresh)
        if df is None or df.empty:
            log.info(f"{n}: empty tape — skipped")
            continue
        out[n] = engine.make_frame(U, df)
        log.info(f"{n}: {len(df):,} rows · {df['fixture'].nunique():,} fixtures · train/test cutoff "
                 f"{out[n].cutoff:%Y-%m-%d} · {time.time() - t:.1f}s")
    return out


def _row(r):
    tr, te = r["train"], r["test"]
    return (f"{r['spec']['name'][:78]:<78} train n={tr.get('n', 0):>4} {_pct(tr.get('yield'))} "
            f"p={tr.get('p', 1):.3f} q={r.get('q', 1):.2f} | test n={te.get('n', 0):>4} "
            f"{_pct(te.get('yield'))} p={te.get('p', 1):.3f}")


def _report(results):
    for u in sorted({r["spec"]["universe"] for r in results}):
        rs = [r for r in results if r["spec"]["universe"] == u]
        tested = [r for r in rs if r["tested"]]
        sig = [r for r in tested if r["train"]["p"] < 0.05]
        passed = [r for r in tested if r["pass"]]
        strong = [r for r in passed if r["strong"]]
        print(f"\n== {u}: {len(rs):,} specs · {len(tested):,} with n ≥ {UNIVERSES[u].min_n_train} on train · "
              f"{len(sig)} at p<0.05 (chance alone ≈ {0.05 * len(tested):.0f}) · "
              f"{len(passed)} PASS out-of-sample · {len(strong)} strong")
        key = (lambda r: (r["test"].get("yield") or 0) * (r["test"].get("n") or 0) ** 0.5)
        show = sorted(passed, key=key, reverse=True)[:12]
        if show:
            for r in show:
                print("  ✓ " + _row(r))
        else:
            best = sorted(tested, key=lambda r: r["train"]["p"])[:5]
            for r in best:
                print("  · " + _row(r))


def _csv(results) -> str:
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"factory_grid_{datetime.now(timezone.utc):%Y-%m-%d_%H%M}.csv")
    cols = ["universe", "template", "name", "tested", "pass", "strong", "q",
            "train_n", "train_yield", "train_ci_lo", "train_p", "test_n", "test_yield", "test_p",
            "cal_fixtures", "cal_pp", "cal_ci_lo_pp", "avg_odds", "hit", "implied", "spec_hash", "spec"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in results:
            s, tr, te, ca, al = r["spec"], r["train"], r["test"], r["cal"], r["all"]
            w.writerow([s["universe"], s.get("template"), s["name"], r["tested"], r["pass"], r["strong"],
                        r.get("q"), tr.get("n", 0), tr.get("yield"), tr.get("ci_lo"), tr.get("p"),
                        te.get("n", 0), te.get("yield"), te.get("p"), ca.get("fixtures"), ca.get("pp"),
                        ca.get("ci_lo_pp"), al.get("avg_odds"), al.get("hit"), al.get("implied"),
                        spec_hash(s), json.dumps(s)])
    return path


def cmd_universes(_):
    for U in UNIVERSES.values():
        print(f"\n{U.name}  [{U.price_source}{', LIVE' if U.live else ''}]  split {U.split}  "
              f"min n {U.min_n_train}/{U.min_n_test}\n  {U.description}")
        if U.market_sides:
            print("  markets: " + "; ".join(f"{m} → {', '.join(s)}" for m, s in U.market_sides.items()))
        print(f"  exits: {', '.join(U.exits)}\n  features: {', '.join(U.features)}")


def cmd_build(args):
    _frames(registry.connect(), list(UNIVERSES), refresh=True)


def cmd_grid(args):
    names = args.universe or list(grids.TEMPLATES)
    conn = registry.connect()
    frames = _frames(conn, names, args.refresh)
    specs = grids.all_specs([n for n in names if n in frames])
    t = time.time()
    results = engine.run_batch(specs, frames)
    log.info(f"{len(specs):,} specs backtested in {time.time() - t:.0f}s")
    _report(results)
    print(f"\nfull grid → {_csv(results)}")
    if args.register:
        print("registered:", registry.upsert_results(conn, results), registry.upsert_explore(conn, results))


def cmd_backtest(args):
    raw = args.spec
    if os.path.exists(raw):
        with open(raw) as fh:
            raw = fh.read()
    try:
        spec = normalize(json.loads(raw))
    except (SpecError, ValueError) as exc:
        sys.exit(f"bad spec: {exc}")
    conn = registry.connect()
    fr = _frames(conn, [spec["universe"]])[spec["universe"]]
    r = engine.run_batch([spec], {spec["universe"]: fr})[0]
    print(json.dumps({k: r[k] for k in ("spec", "all", "train", "test", "cal", "cash_out_fallbacks", "cutoff")},
                     indent=2, default=str))


def cmd_leaderboard(args):
    rows = registry.leaderboard(registry.connect(), args.limit)
    if not rows:
        print("no strategies registered yet — run: python factory_cli.py grid --register")
        return
    print(f"{'id':>5} {'status':<9} {'fwd n':>6} {'fwd yield':>10} {'CI lo':>8} │ {'bt test n':>9} "
          f"{'bt test':>8} {'q':>5}  name")
    for r in rows:
        print(f"{r['id']:>5} {r['status']:<9} {r['fwd_n']:>6} {_pct(float(r['fwd_yield']) if r['fwd_yield'] is not None else None):>10} "
              f"{_pct(float(r['fwd_ci_lo']) if r['fwd_ci_lo'] is not None else None):>8} │ "
              f"{r['bt_test_n'] or 0:>9} {_pct(r['bt_test_y']):>8} {r['q'] or 0:>5.2f}  {r['name'][:70]}")


def _locked(name):
    fh = open(os.path.join(HERE, f".factory_{name}.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    return fh


def cmd_run(args):
    lock = _locked("run")
    if lock is None:
        return
    n = runner.run_live(registry.connect(), dry_run=args.dry_run)
    if n:
        log.info(f"{n} paper entries{' (dry run)' if args.dry_run else ''}")


def cmd_settle(args):
    lock = _locked("settle")
    if lock is None:
        return
    n = runner.settle(registry.connect())
    log.info(f"settled {n} paper trades")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universes")
    sub.add_parser("build")
    g = sub.add_parser("grid")
    g.add_argument("--universe", action="append", choices=sorted(UNIVERSES))
    g.add_argument("--register", action="store_true")
    g.add_argument("--refresh", action="store_true")
    b = sub.add_parser("backtest")
    b.add_argument("--spec", required=True)
    lb = sub.add_parser("leaderboard")
    lb.add_argument("--limit", type=int, default=30)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("settle")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [factory] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", force=True)
    {"universes": cmd_universes, "build": cmd_build, "grid": cmd_grid, "backtest": cmd_backtest,
     "leaderboard": cmd_leaderboard, "run": cmd_run, "settle": cmd_settle}[args.cmd](args)


if __name__ == "__main__":
    main()
