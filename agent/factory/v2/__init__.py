"""
Factory v2 — pre-match. Built on the 2026-10-10 audit of v1, which found the
grid structurally blind rather than the market empty: with thousands of specs
judged on realised yield, a spec needed +11pp (pre-match) to +38pp (in-play)
to pass, against edges that are 1-5pp when they exist.

What changes, and why:

    tape.py       one row per match × market × side: the Pinnacle pre-close
                  (the entry), the Pinnacle close, the result, v1's features,
                  and close_model's team state (all from earlier matches)
    stats.py      clustered OLS + Wald, empirical-Bayes shrinkage, power, the
                  three ways to buy (Pinnacle raw · PM taker · PM maker), CLV
    families.py   one regression per CLASS of information before any filter:
                  ~30 tests with ~100k rows each, instead of thousands with ~200
    search.py     specs on CLV or yield, a power gate that refuses what the data
                  cannot resolve, shrinkage, top-K on train → validation
    protocol.py   train → validation → a holdout that only `confirm` reads,
                  with every read written to holdout_ledger.jsonl

CLI: python factory_cli.py v2 {build,families,search,confirm,ledger}
"""
