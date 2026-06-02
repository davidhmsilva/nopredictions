---
name: np-dc-retrain
description: Safely retrain, validate and deploy the NOPREDICTIONS Dixon-Coles model. Use when retraining the DC model, after ingesting new match data, or whenever DC/Sim predictions look wrong (e.g. ~50% home for every game). Enforces convergence + strength-spread + sanity checks BEFORE deploying to the live-money path.
---

# Safe DC Model Retrain

The DC model (`agent/dc_model_params.json`) prices the DC and Sim live-money
strategies. A bad retrain bleeds real money. On 2026-05-25 a retrain silently
shipped a **non-converged** fit (strength std 0.047, ~53% home for every match)
because L-BFGS-B used a numerical gradient and ran out of evals after ~5
iterations. It traded real money for a week. Never deploy a fit blind again.

## Golden rules

- **Never overwrite `agent/dc_model_params.json` (or `site/app/lib/dc_params.json`) without validating first.** Train to a temp file, validate, then copy.
- A healthy fit has: `fit_success=True`, **strength std ≈ 0.30–0.35**, `home_adv ≈ 0.23–0.30`, `rho` slightly **negative** (≈ −0.10). `dc_trainer.py` now refuses to save a non-converged or degenerate (std < 0.15) fit, but still eyeball it.
- The current optimiser uses an **analytic gradient** (in `dixon_coles.fit()`); if you touch that math, re-verify it against finite differences (`/tmp/dc_grad_test.py` pattern, max err must be < 1e-4) before trusting any fit.

## Workflow

1. **Train to a temp file** (never straight to production):
   ```bash
   cd agent && ../ingest/.venv/bin/python dc_trainer.py --l2-reg 1.0 --output /tmp/dc_candidate.json
   ```
   `--l2-reg 1.0` reproduces the pre-break strength spread and tames sparse
   national-team over-fit. Lower → sharper clubs but wilder minnows; higher →
   compressed (toward the broken state). Optimal value should come from
   walk-forward validation, not eyeballing.

2. **Read the trainer output** and confirm: `Strength spread (std)` ≈ 0.30, sane
   `home_adv`/`rho`, and `Brier (model)` close to `Brier (Pinnacle)` (gap ≲ 0.013).

3. **Sanity-check predictions** against the candidate file — strong teams must
   dominate weak ones:
   ```bash
   cd agent && ../ingest/.venv/bin/python - <<'PY'
   import sys; sys.path.insert(0,'.')
   from dixon_coles import DixonColesModel
   from dc_scanner import _norm, _find_team
   m=DixonColesModel.load('/tmp/dc_candidate.json'); idx={_norm(t):i for i,t in enumerate(m.teams)}
   for h,a in [("Manchester City","Sheffield United"),("Argentina","Jordan"),("Brazil","Scotland"),("Spain","Japan")]:
       hi,ai=_find_team(h,idx),_find_team(a,idx); p=m.predict(m.teams[hi],m.teams[ai])
       print(f"{h} v {a}: home {p['home_win']*100:.0f}% (λ {p['lambda_home']:.2f}/{p['lambda_away']:.2f})")
   PY
   ```
   Expect Man City ~90%, Argentina ~90%, Brazil ~74%. If everything is ~53%, the
   fit is collapsed — STOP, do not deploy.

4. **Deploy only if all checks pass** — copy to both the agent and the site:
   ```bash
   cp /tmp/dc_candidate.json agent/dc_model_params.json
   cp /tmp/dc_candidate.json site/app/lib/dc_params.json
   ```

5. **Tell the user** the new std / home_adv / rho and the sanity numbers, and that
   the next cron (08:00 UTC) will use it. The site needs a manual Vercel deploy
   (`cd site && vercel --prod --yes`) to update the public scanner.

## Known limitation
DC has no league-strength term and few games per national team, so it over-rates
international home underdogs by +20–30pp even when healthy. The sharp gate vetoes
these and we don't bet internationals — but ideally the scanners should skip
international competitions entirely.
