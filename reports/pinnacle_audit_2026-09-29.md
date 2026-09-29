# Measuring Pinnacle's close instead of trusting it — 2026-09-29

Source: Football-Data raw CSVs, 22 leagues, **101,469 matches, 2012-13 → 2025-26**
(the same set as `bt_features`). Pinnacle close on every match; market-max
close, O/U 2.5 and AH close from 2019-20. `p` = multiplicative de-vig of
Pinnacle's close; **cal** = hit rate − p; **ROI** is always at the real quoted
odds. Train = seasons 2012-2019, test = 2020 onward.
Reproduce: `cd agent && python pinnacle_audit.py --calibration --scan --clubs`.

## 1. Where the close is mis-calibrated

**Favourite–longshot, and it replicates.**

| close | cal train | cal test | ROI @Pinnacle test | ROI @best price (2019+) |
|---|--:|--:|--:|--:|
| 1.0-1.3 | +3.36pp | +2.43pp | −0.8% | **+2.1% ±1.9** |
| 1.3-1.6 | +0.90 | +3.16 | +1.1% | **+3.2% ±1.7** |
| 1.6-2.0 | +0.02 | +0.46 | −2.7% | −0.3% |
| 3.5-5.0 | −0.40 | −0.65 | −6.0% | −2.9% |
| 8-15 | −0.86 | −2.03 | −24.0% | −17.8% |
| 15+ | −1.82 | −1.24 | −32.1% | −15.8% |

- Heavy favourites win more often than the de-vigged close says. At Pinnacle's
  own price that is break-even, but at the **best price across books** it is
  +2 to +3%. Caveat: soft books limit winning accounts.
- Away longshots at 8.0+ win less often than priced. **Drifting** away 8.0+ in
  top divisions: −3.9pp train / −4.1pp test (CI ±2.0). Backing them loses 51-57%.
  The other side of that is buying **No** on Polymarket (see H-LONGSHOT-NO).
- O/U 2.5 (2019+) has the same shape: the favoured side of the total runs
  +1.6pp and the longshot side −1.6pp. Nothing is profitable at the close.

**What did NOT replicate** (778 cells in `--scan`, 21 significant on train at BH q<0.10, 9 replicate on test —
all of them favourite/longshot cells): early season, run-in,
promoted and relegated teams, league identity, and line movement other than
longshot drift. These are negatives for this method (a season-phase or status
flag against the close), not proof the close has them right: team news,
line-ups and in-season ratings were not in the test.

## 2. Teams

- Team-season residual → next season: corr **0.004** (4,455 pairs). First half →
  second half of the same season: **−0.001** (5,545). Picking the top decile of
  teams on train and backing them on test: −2.7%. **"This team beats the
  market" in general does not persist.**
- **Except one thing: specific clubs as HEAVY favourites.** Train → test corr
  **0.34** across 42 clubs (0.08 for teams in general).

### H-FAV-CLUBS — the strategy (id 42, pre-registered)

Each season, using only earlier seasons: clubs with ≥40 heavy-favourite games
(close < 1.60) whose shrunk residual `r·n/(n+60)` is ≥ +4pp. Back them to win
whenever they close under 1.60.

| | n | ROI @Pinnacle close | ROI @best price |
|---|--:|--:|--:|
| **strategy, 2015-2025** | 1,792 | **+5.91% ±2.56** | +7.65% |
| strategy, 2020-2025 only | 1,052 | +5.43% ±3.40 | +8.47% |
| control: every heavy favourite | 13,891 | +0.01% | +2.66% |

- **10 of 11 seasons positive**: 2015 +14.8, 2016 +4.1, 2017 +7.6, 2018 +10.7,
  2019 −0.1, 2020 +6.1, 2021 +5.7, 2022 +4.7, 2023 +7.9, 2024 +1.0, 2025 +9.1.
- Team-clustered bootstrap CI **[+2.6%, +9.0%]**.
- Without the top-3 contributors (Arsenal, Juventus, Galatasaray): **+3.6% ±3.0**.
- Home +5.3%, away +7.4%. By league: England +13.8, Italy +11.2, Spain +9.3,
  Turkey +6.9, Greece +6.1, Portugal +2.3, Belgium +1.0.
- Robust to the two thresholds tried (≥2pp / ≥4pp) and cannot be reproduced
  by lowering the band: under 2.2 it is break-even at Pinnacle.

**Picks for 2026-27** (from 2012-2025 only): Arsenal, Galatasaray, Olympiakos,
Juventus, Atlético Madrid, Sevilla, Sporting CP, Barnet, Union SG, Leicester,
Beşiktaş, Roma, Brentford, Cove Rangers, Tottenham, Benfica, Atromitos, Luton,
Aberdeen, Fenerbahçe, Antwerp.

**Forward so far (2026-27, Betfair close − 2%):** 42 bets, 31 won,
**−0.4% ±18.6**. That is too early to say anything either way.

Not established: the mechanism. Candidates are public or recreational money
against dominant clubs in lopsided leagues, and Pinnacle's model lagging a
club's squad strength. What would kill it: the forward CI settling below zero
over ≥300 bets.

## 3. A data fact found on the way

⚠️ **Football-Data's 2026-27 files carry no Pinnacle columns at all** (PSH, PSCH
gone). They now carry Betfair Exchange open/close (BFE, BFEC), Betfair
Sportsbook, Pinnacle-less max/avg, **and team xG (HxG/AxG)**. Everything in the
site and agents that reads "Pinnacle (closing)" gets nothing for this season.
Stage A needs to store BFEC (and the xG) instead.

## 4. Next

1. **Polymarket's own calibration.** Run the `pm_results` backfill (March →
   now, ~180 days × ~90 fixtures), then run the same band table on PM's
   kick-off price. That is where H-LONGSHOT-NO is decided, and it shows whether
   PM also underprices heavy favourites.
2. Paper-trade H-FAV-CLUBS on PM (CLOB ask ≤45' before kick-off) beside the
   Betfair-close record.
3. Stage A: load BFEC + xG for 2026-27.
