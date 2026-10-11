"""
protocol.py — train → validation → a holdout that is read on purpose, once.

    families  every (family, view, target) fitted on TRAIN; Benjamini-Hochberg
              across all of them; the ones that pass are projected onto
              VALIDATION (Bonferroni over how many passed)
    search    the runs in search.py, TRAIN then VALIDATION
    prereg    what survived validation, written to reports/ as a list —
              decided before the holdout is touched
    confirm   the only function that reads holdout rows. It evaluates exactly
              the list it is given, Bonferroni over its length, and appends
              one line to holdout_ledger.jsonl (committed to git), so how
              often the holdout has been read is a matter of record. A list
              already in the ledger is answered from the ledger.

⚠️ The 2022-07+ holdout is not virgin. v1's test period, pinnacle_audit and
close_model all read it. It checks that the protocol works; the clean
confirmation of anything found here is forward data.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import families as fm
from . import search as sr
from . import stats

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "reports"))
LEDGER = os.path.join(HERE, "holdout_ledger.jsonl")
FDR_Q = 0.10
DEFAULT_RUNS = [("clv", "pin_raw"), ("clv", "pm_taker"),
                ("yield", "pin_raw"), ("yield", "pm_taker"), ("yield", "pm_maker_strict")]
HOLDOUT_NOTE = ("the 2022-07+ holdout is not virgin (v1's test period, pinnacle_audit and close_model "
                "read it): it checks the protocol; forward data is the clean confirmation")


def development(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train and validation rows. Nothing that searches may see the holdout."""
    return df[df.split == "train"], df[df.split == "validation"]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")


# ── families ────────────────────────────────────────────────────────────────

def run_families(df: pd.DataFrame, fdr_q: float = FDR_Q) -> list:
    train, valid = development(df)
    out = []
    for fam, view, target in fm.tests():
        tr, va = train[train.view == view], valid[valid.view == view]
        leagues = fm.leagues_of(tr)
        r = fm.fit_family(tr, view, target, fam, leagues)
        out.append({"family": fam.name, "view": view, "target": target, "leagues": leagues,
                    "train": r, "info": fam.info, "_va": va})
    tested = [o for o in out if "p" in o["train"]]
    for o, q in zip(tested, stats.bh_qvalues([o["train"]["p"] for o in tested])):
        o["q"] = q
    promoted = [o for o in tested if o["q"] <= fdr_q]
    alpha = 0.05 / max(len(promoted), 1)
    fams = {f.name: f for f in fm.FAMILIES}
    for o in out:
        va = o.pop("_va")
        if o in promoted:
            o["valid"] = fm.project(va, o["view"], o["target"], fams[o["family"]], o["leagues"],
                                    o["train"]["beta_f"])
            o["survived"] = bool(o["valid"]["gamma"] > 0 and o["valid"]["p"] < alpha)
        else:
            o["survived"] = False
        o["alpha_valid"] = alpha
    return out


def print_families(res: list) -> None:
    print(f"\n{'family':<13} {'view':<10} {'target':<10} {'matches':>8} {'χ²/df':>8} {'p train':>9} {'q':>6} │"
          f" {'γ valid':>8} {'p valid':>9} {'R² gain':>8}  verdict")
    for o in sorted(res, key=lambda o: (not o["survived"], o.get("q", 9))):
        t = o["train"]
        if "p" not in t:
            continue
        v = o.get("valid") or {}
        verdict = ("SURVIVES" if o["survived"] else
                   "fails validation" if v else "not significant on train")
        gam = f"{v['gamma']:+.2f}" if v else ""
        pv = f"{v['p']:.1e}" if v else ""
        r2 = f"{100 * v['r2_gain']:.3f}%" if v else ""
        print(f"{o['family']:<13} {o['view']:<10} {o['target']:<10} {t['matches']:>8,} "
              f"{t['chi2'] / t['df']:>8.1f} {t['p']:>9.1e} {o['q']:>6.3f} │ {gam:>8} {pv:>9} {r2:>8}  {verdict}")


# ── search ──────────────────────────────────────────────────────────────────

def run_search(df: pd.DataFrame, runs=None, target: float | None = None, k: int = sr.DEFAULT_K) -> list:
    train, valid = development(df)
    specs = sr.generate(train)
    return [sr.run(train, valid, specs, obj, ex, target, k) for obj, ex in (runs or DEFAULT_RUNS)]


def _pp(x) -> str:
    return "   —  " if x is None else f"{100 * x:+.2f}%"


def print_search(results: list) -> None:
    for r in results:
        print(f"\n== {r['objective']} @ {r['execution']}: {r['specs']} specs · {r['gated']} refused by the power "
              f"gate (MDE > {100 * r['target']:.0f}pp, n < {sr.MIN_N}, or duplicate) · {r['tested']} tested · "
              f"{len(r['picked'])} picked → {len(r['survivors'])} survive validation (α {r['alpha_valid']:.4f})")
        for p in r["picked"]:
            t, v = p["train"], p.get("valid", {})
            mark = "✓" if p["validated"] else "·"
            print(f"  {mark} {p['spec']['name'][:64]:<64} train n={t['n']:>5} {_pp(t['mean'])} "
                  f"(post {_pp(p['post'])}) │ valid n={v.get('n', 0):>5} {_pp(v.get('mean'))} p={v.get('p', 1):.4f}")
        if not r["picked"]:
            best = sorted((x for x in r["rows"] if not x["gated"]), key=lambda x: x.get("post", -9), reverse=True)[:3]
            for p in best:
                print(f"  · best posterior is negative: {p['spec']['name'][:60]} train {_pp(p['train']['mean'])} "
                      f"post {_pp(p.get('post'))}")


def search_csv(results: list) -> str:
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"factory_v2_search_{_stamp()}.csv")
    recs = []
    for r in results:
        for x in r["rows"]:
            t, v = x["train"], x.get("valid", {})
            recs.append({"objective": r["objective"], "execution": r["execution"], "template": x["spec"]["template"],
                         "name": x["spec"]["name"], "id": x["id"], "gated": x["gated"],
                         "train_n": t["n"], "train_mean": t["mean"], "train_se": t["se"], "mde": x["mde"],
                         "post": x.get("post"), "picked": x in r["picked"], "valid_n": v.get("n"),
                         "valid_mean": v.get("mean"), "valid_p": v.get("p"), "validated": x.get("validated", False),
                         "spec": json.dumps(x["spec"])})
    pd.DataFrame(recs).to_csv(path, index=False)
    return path


# ── preregistration and the holdout ─────────────────────────────────────────

def prereg_items(fam_res: list, search_res: list) -> list:
    items = []
    for o in fam_res:
        if o["survived"]:
            items.append({"kind": "family", "family": o["family"], "view": o["view"], "target": o["target"],
                          "train": {k: o["train"][k] for k in ("matches", "chi2", "df", "p")},
                          "valid": {k: o["valid"][k] for k in ("matches", "gamma", "p", "r2_gain")}})
    for r in search_res:
        for p in r["survivors"]:
            items.append({"kind": "spec", "objective": r["objective"], "execution": r["execution"],
                          "id": p["id"], "spec": p["spec"], "train": p["train"], "valid": p["valid"]})
    return items


def write_prereg(items: list, note: str = "") -> str:
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"factory_v2_prereg_{_stamp()}.json")
    with open(path, "w") as fh:
        json.dump({"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": note,
                   "alpha_each": 0.05 / max(len(items), 1), "items": items}, fh, indent=1, default=float)
    return path


def ledger(path: str | None = None) -> list:
    path = path or LEDGER
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _git_sha() -> str | None:
    """HEAD, marked +dirty when the code that produced the answer is not committed."""
    try:
        run = lambda *a: subprocess.run(["git", *a], cwd=HERE, capture_output=True,  # noqa: E731
                                        text=True, timeout=5).stdout.strip()
        sha = run("rev-parse", "--short", "HEAD")
        dirty = run("status", "--porcelain", "--", ".", ":(exclude)holdout_ledger.jsonl")
        return (sha + ("+dirty" if dirty else "")) or None
    except (OSError, subprocess.SubprocessError):
        return None


def confirm(df: pd.DataFrame, prereg_path: str, ledger_path: str | None = None) -> dict:
    """Read the holdout for exactly the preregistered list, once."""
    ledger_path = ledger_path or LEDGER
    raw = open(prereg_path, "rb").read()
    sha = hashlib.sha1(raw).hexdigest()[:16]
    past = ledger(ledger_path)
    for e in past:
        if e["prereg_sha"] == sha:
            return {**e, "from_ledger": True}
    items = json.loads(raw)["items"]
    hold = df[df.split == "holdout"]
    dev = df[df.split.isin(["train", "validation"])]
    alpha = 0.05 / max(len(items), 1)
    fams = {f.name: f for f in fm.FAMILIES}
    results = []
    for it in items:
        if it["kind"] == "family":
            fam, view, target = fams[it["family"]], it["view"], it["target"]
            dv, ho = dev[dev.view == view], hold[hold.view == view]
            leagues = fm.leagues_of(dv)
            fit = fm.fit_family(dv, view, target, fam, leagues)
            h = fm.project(ho, view, target, fam, leagues, fit["beta_f"])
            res = {"n": h["n"], "matches": h.get("matches"), "estimate": h.get("gamma"), "p": h["p"],
                   "r2_gain": h.get("r2_gain")}
            res["pass"] = bool((res["estimate"] or 0) > 0 and res["p"] < alpha)
            label = f"family {it['family']} · {view} · {target}"
        else:
            r = sr.returns(hold, it["objective"], it["execution"])[sr.mask(hold, it["spec"])]
            t = stats.mean_test(r)
            res = {"n": t["n"], "estimate": t["mean"], "se": t["se"], "p": t["p"]}
            res["pass"] = bool((t["mean"] or 0) > 0 and t["p"] < alpha)
            label = f"{it['objective']}@{it['execution']} · {it['spec']['name']}"
        results.append({"label": label, **res})
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "round": len(past) + 1,
             "prereg": os.path.basename(prereg_path), "prereg_sha": sha, "git": _git_sha(),
             "items": len(items), "alpha_each": alpha, "passed": sum(r["pass"] for r in results),
             "results": results, "note": HOLDOUT_NOTE}
    with open(ledger_path, "a") as fh:
        fh.write(json.dumps(entry, default=float) + "\n")
    return entry


def print_confirm(entry: dict) -> None:
    src = " (already in the ledger — nothing new was read)" if entry.get("from_ledger") else ""
    print(f"\nholdout round {entry['round']}{src}: {entry['items']} preregistered · α each {entry['alpha_each']:.4f}"
          f" · {entry['passed']} pass\n  ⚠️ {entry['note']}")
    for r in entry["results"]:
        est = r.get("estimate")
        est_s = "—" if est is None else (f"{100 * est:+.2f}%" if r["label"].startswith(("clv", "yield")) else f"γ {est:+.2f}")
        print(f"  {'✓' if r['pass'] else '✗'} {r['label'][:80]:<80} n={r['n']:>6} {est_s:>9} p={r['p']:.1e}")
