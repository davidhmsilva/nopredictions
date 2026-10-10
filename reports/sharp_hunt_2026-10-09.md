# Sharp hunt — 10 new Polymarket sports wallets (2026-10-09)

Every sport, excluding every wallet studied before (GSX-, king1605, Grand-Vista,
BreakTheBank, swisstony, `0x2c33…`, RN1, gravia.trade). Scripts, the scored
table and all 24 full profiles: `reports/sharp_hunt_2026-10-09/`.

## Method

| stage | what | in → out |
|---|---|---|
| 1 pool | `data-api /v1/leaderboard?category=SPORTS`, PNL order: ALL top 3,000 · MONTH 1,500 · WEEK 500 | 4,074 wallets |
| 1 filter | all-time sports P&L ≥ $25k, volume ≥ $250k, **still profitable this month** | 369 |
| 2 screen | last ≤600 closed positions **+ unredeemed resolved positions** in the same window; per-event clustered bootstrap of per-share edge and $-ROI; ≥100 events | 292 scored, 38 with both CIs > 0 |
| 3 verify | ≥200 events, top-5 events < 62% of P&L → `wallet_analyzer.py` full FIFO from `/activity` | 24 profiled |

🔑 **`/closed-positions` alone is winner-biased.** A losing token is worth 0 and
nobody redeems it, so it never closes. It stays in `/positions` with
`redeemable: true, curPrice: 0`. Stage 2 on closed positions alone put 183
wallets at **+40-55pp per share and ~+100% ROI** with tight CIs. With the losers
added back, 38 survived, at +2 to +16pp. Any leaderboard or screener built
on `/closed-positions` shows this artefact.

The ranking metric is the event-clustered yield CI from the full FIFO, not P&L.
"Unreconciled" below means the analyser's 1% tolerance failed. On every wallet
listed, the reconstruction sits within 3-15% of PM's own gross profit; the two
where it does not are dropped (see the end).

## The 10

| # | wallet | sport | what it does | events | deployed | yield [95% CI] |
|---|---|---|---|--:|--:|---|
| 1 | **zhiber** `0x82307f44c9405e73dc1cff466073dcc505535121` | 78% football + NBA | buys stale/cheap orders in-play and after the whistle, vwap **0.14** | 1,299 | $150k | **+69.8% [+45.9, +100.1]** |
| 2 | **Oliveira39** `0x54a2c4cfc4332d831acc3f5a860d6540982c1d43` | 77% football | same shape as zhiber (vwap 0.15), market overlap 1% | 1,235 | $185k | **+53.1% [+32.7, +74.9]** |
| 3 | **spffast** `0xd60c6fa3d340c3f7d98ec939b45492daf77d2b06` | 100% football | in-play scalper, median hold **1 min**, 71% exit by selling; 196/217 days green; `trust = ok` | 5,627 | $180k | **+24.6% [+21.6, +27.9]** |
| 4 | **0xD5BF…** `0xd5bf2e7a0871eccfecf7dc315deca1326d54f0cc` | 98% football | in-play scalper with **24% of cost after the whistle** | 1,625 | $114k | **+28.8% [+23.8, +34.1]** |
| 5 | **drop1234** `0xc66ab53d0ae32949a32b82ab1c4671519ca0ed6a` | 95% football | the scalper at **10× the size**, earns rebates | 1,537 | $1.40M | **+10.0% [+7.8, +12.4]** — $140k |
| 6 | **Betyourself** `0xa991049a265f0f29f8a1a8856b170ad799c10935` | 81% football | in-play, mostly held to settlement (16% sold) | 3,165 | $313k | **+10.3% [+7.2, +13.5]** |
| 7 | (no name) `0x5139cbd66f371e1916f0e0156671cc950f0ad446` | tennis, 65% of P&L ITF | near-certainty grinder, vwap 0.87 | 517 | $224k | **+11.3% [+6.6, +16.7]** — only 52 days |
| 8 | **royalestake** `0xfb681e23db8d1cca8a6b7e2a70a29e000ddfa240` | 76% NHL | **pre-match**, holds ~44h to settlement, 413 days | 781 | $2.46M | **+10.0% [+1.7, +18.2]** — $246k |
| 9 | **Tys0n8686** `0x52ed504e3c3c7cfceaa61dc4f23a6e29d79f8db7` | 53% NBA | **pre-match** position taker, 333 days | 716 | $2.04M | **+4.2% [+2.2, +6.8]** |
| 10 | **Fiscus** `0x1d3fd83676aabe718c13cba008e0a774b2127fec` | futures + UFC | near-certainty grinder, vwap 0.89 | 1,161 | $426k | **+10.0% [+5.8, +15.4]** — top 5 events = 46% |

Reserve: **maxiplayer** `0x40b959d8f1823a3f01b1d6fdf144594918f5a596`: post-whistle
sweeper at size, +3.1% [+2.1, +4.3] on $1.89M. **hihhe**
`0x790e44a5056151c832ae0cbac71b249f0b6b55d6`: tennis at 0.99, +1.2% [+1.0, +1.4]
on $2.32M, 67/68 green days. That is picking up pennies, with the tail never
seen in 68 days.

## The in-play scalper family: nine accounts, one fingerprint

spffast (#3) is the one fully reconciled member of a group with the same profile:
100% football, median hold 1-2 min, 91-94% of cost in-play, 70-85% exit by
selling, buy vwap 0.36-0.44, top-5 events ≤ 11%, yield 22-28% with CIs ~±4pp.

| wallet | address | span | yield [CI] | P&L |
|---|---|--:|---|--:|
| polika72 | `0x13997bdbf1b291b7ba65afaf1f0d8e4719ee48c8` | 211d | +27.8% [+24.7, +31.2] | $63k |
| blackewolf83 | `0x09b045baad1fbe115c70785635a261411774a3b6` | 135d | +26.7% [+22.8, +30.8] | $51k |
| jack.jr | `0x1985327e5782c62362dbbdf714c423d40d8f51ab` | **126d** | +25.9% [+21.7, +30.4] | $47k |
| theZok1 | `0xd970693a3384dc762b191707a4927ac3814bbbba` | 197d | +25.0% [+21.0, +29.2] | $49k |
| matmorfo | `0x03c9e3c68c9ef00ea94e2acf08d581a5e56bd8c1` | **126d** | +25.7% [+20.9, +30.7] | $34k |
| romulux | `0x153a9a2f23f780cd74a051de526a813da603af29` | **126d** | +24.8% [+20.2, +29.3] | $31k |
| foxchaser | `0x3968f7c9fd10697665c3b0b92a8c9b18462985b5` | **126d** | +24.9% [+19.9, +30.2] | $34k |
| dekadurabolin | `0xa42f36484754c0740aa419aac907afa4501cb629` | **126d** | +22.0% [+17.2, +27.0] | $27k |

Five accounts started on the same day. Pairwise market overlap is only 0-15%,
which fits one operator splitting the board across accounts rather than nine
people copying each other. That is not proven. Together with spffast: **~$380k
at ~25% on ~$1.5M deployed**, 100% in-play football. This is the strategy our
own in-play work never found. It is the next wallet to study in depth: which
markets, which minute, which side, and where its profit comes from.

## What is not established

- **Selection.** 292 survivors of a leaderboard were screened, so ~7 would
  clear a 95% CI by chance. Rows 1-7 and the family clear it by a margin no
  multiple-testing correction touches (CI floors +6 to +46%). Rows 8-10 and
  the reserve (floors +1.7 to +5.8%) are candidates only.
- **Pre-match rows (8, 9) need CLV**, which is the right test for that
  archetype and was not run. Their yield CIs are wide relative to the effect.
- **The scalpers' yield is not copyable as-is.** A 1-minute hold means the
  edge is in execution speed. Copying at +1 min may keep none of it; that is
  the Grand-Vista test, and it has to be run per wallet.
- **Reconciliation** sits between 1% and 15%. It is close enough to rank on,
  but not `ok`.

## Dropped at stage 3

`_test_i075_i078_1` (reconstructed $85k vs PM $55k) and SchruteBucks ($91k vs
$45k) do not reconcile. Nomadxl (227 events, 45/77 green days) and arman1969
(top 5 = 54%, CI [+4.3, +28.5]) are too concentrated. 033033033 was dropped
before stage 3: +0.2% on $139M, mostly politics, a market maker.
