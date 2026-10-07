# Tokens comparáveis: prediction markets, ferramentas de pesquisa e ferramentas de trading
Pesquisa feita a **7 out 2026, cerca das 20:50 CEST**, para o No Predictions.

**Fontes de market cap (MC):**
- CoinGecko API `/coins/markets`, pedido feito às **20:51 CEST** (dados com `last_updated` de 18:49Z, ou seja **20:49 CEST**). As páginas estão em https://www.coingecko.com/en/coins/<id>.
- CrawlScan: GeckoTerminal API, às **20:53 CEST**: https://www.geckoterminal.com/robinhood/tokens/0x19dCb63C4d2F29A6f077F094a4f858fC790145e1

**Notas de leitura:**
- MC = preço × oferta em circulação. FDV = preço × oferta total. "ATH" é o preço máximo histórico segundo o CoinGecko, e "Δ ATH" é a queda desde esse máximo.
- **[est.]** é um MC no ATH estimado por mim (preço ATH × oferta atual), não um dado publicado.
- **[n/v]** quer dizer não verificado nesta pesquisa.
- Os MC muito pequenos com volume quase nulo (menos de $100/24h) valem pouco como preço real.

---

## A) Prediction markets com token (estabelecidos e pequenos)

| Projeto | Ticker | Chain | Produto | Tipo de lançamento | Data | MC hoje | ATH (data) · Δ ATH | Utilidade | Nota |
|---|---|---|---|---|---|---|---|---|---|
| Polymarket | — (POLY não existe) | Polygon | Maior prediction market | Ainda sem token | — | n/a | — | — | O CMO confirmou em out 2025 que "there will be a token". A 7 out 2026, na TOKEN2049, o CEO só falou de um "onchain asset" com "programmable utility", sem lançar nada. O id `polymarket` no CoinGecko não tem dados. |
| Kalshi | — | — | Prediction market regulado nos EUA | Sem token nativo | — | n/a | — | — | Os "KALSHI" no CoinGecko são ações tokenizadas/pré-IPO, não um token do projeto. |
| Gnosis | GNO | Ethereum / Gnosis Chain | Nasceu como prediction market; hoje é uma chain + ecossistema | ICO 2017 [n/v] | 2017 | **$317,6M** | $644,20 (8 nov 2021) · −81% | Staking de validadores | Projeto antigo e muito diversificado, não é comparável a um token pequeno. |
| Drift (agora Velocity) | DRIFT | Solana | DEX de perps + prediction markets "BET" | Airdrop + VC [n/v] | 2024 | **$15,3M** | $2,60 (8 nov 2024) · −99,2% | Governança | Hack de ~$285–295M a 1 abr 2026. Até agora a recuperação devolveu ~1% das perdas. |
| **Overtime** (ex-Thales) | **OVER** | Optimism/Arbitrum/Base/ETH | **Sportsbook on-chain** (desporto) | Migração 1:1 de THALES (Thales existe desde 2021) | abr 2025 | **$11,3M** (FDV igual) | $0,369 (27 set 2025) · **−44%** | **Colateral de apostas com melhores odds + buyback & burn com a receita** | É o comparável desportivo que melhor segurou valor. |
| Thales (legado) | THALES | Optimism/ETH | Token antigo da Overtime | IDO/farming 2021 [n/v] | 2021 | $1,38M | $3,75 (2021) · −94,5% | Migrável para OVER | Volume de $677/24h. |
| Opinion | OPN | BNB Chain | Prediction exchange de macro (YZi Labs) | Airdrop + Binance Launchpool + VC (23% para investidores) | TGE 5 mar 2026 | **$10,6M** (FDV $58,2M) | $0,4646 (5 mar 2026, dia do TGE) · −87,5% | Staking para disputas; acesso/VIP só planeado | O volume caiu 84% de fev para mar 2026, quando acabaram os incentivos do airdrop. |
| Limitless | LMTS | Base | Prediction market diário de cripto e ações | Airdrop (sem vesting) + VC (Coinbase Ventures, 1confirmation) | TGE 22 out 2025 | **$5,42M** (FDV $41,2M) | $0,694 (22 out 2025, dia do TGE) · −94% | Staking e recompensas | O máximo foi no dia do lançamento. |
| **Sport.fun** (ex-Football.fun) | **FUN** | Base (+Solana) | **Fantasy sports**: ações de atletas (futebol, NFL; NBA planeada) | VC (6MV) + ICO a $0,06 (FDV $60M) via Legion/Kraken em 16 dez 2025 | TGE 31 jan 2026 | **$3,54M** (FDV $19,9M) | $0,122 (16 jan 2026*) · −84% (−67% face ao preço da ICO) | Descontos nas fees + **40% da receita vai para buybacks** | Já era rentável antes do TGE. *O ATH é anterior ao TGE (provavelmente pré-mercado) [n/v]. |
| Azuro | AZUR | Polygon/Gnosis/Base | Infraestrutura de apostas desportivas | VC + airdrop [n/v] | meados de 2024 | **$290k** (API) / $312k (página CoinGecko) | $0,2396 (20 jul 2024) · −99,7% | Staking (stAZUR), governança | Infraestrutura desportiva com produto real, mas o token colapsou. |
| SX Bet / SX Network | SX | SX Rollup | **Exchange de apostas desportivas** | — | — | n/d (o CoinGecko não reporta MC) | — | **Token descontinuado** | Snapshot a 15 mai 2026: cada SX foi convertido em Bet Credits a $0,015, sem saque possível. |
| Augur | REP | Ethereum | O primeiro prediction market | Crowdsale 2015 | 2015 | n/d (FDV $1,81M) | $341,85 (2016) · −99,8% | Reporting/disputas | Praticamente sem volume ($207/24h). |
| Polkamarkets | POLK | Polygon/Moonbeam | Prediction markets | IDO 2021 [n/v] | 2021 | $301k | $4,18 (mar 2021) · −99,9% | Governança | Volume de $54/24h. |
| PNP Exchange | PNP | Solana | Prediction markets permissionless com bonding curves e oráculos de IA | Lançado no Believe e migrado para o **pump.fun** (nov 2025) | 2025 | $130k | $0,00478 (25 ago 2025) ≈ $4,8M [est.] · −97% | Pouca utilidade documentada | Lançado pelo dev, estilo memecoin. |
| PRDT Finance | PRDT | BNB | Previsão de preço de cripto/forex | [n/v] | [n/v] | $2,39M | $1,26 (nov 2025) · −92% | [n/v] | Volume de $7,63/24h, por isso o MC não tem significado. |
| **Sports Brackets** | **BRACKETS** | Solana → **Robinhood Chain** | Memecoin de comunidade desportiva (brackets) | Fair launch em Solana (jun 2025), migrado 1:1 para a Robinhood Chain (~ago 2026) | 2025/2026 | $293k | $0,000303 (4 out 2026) · −4,8% | **Meme puro** (categoria "Meme" no CoinGecko) | É desporto + Robinhood Chain, e está perto do máximo. |
| BetSwirl / Zeitgeist / Hedgehog / Myriad / PredX | BETS / ZTG / — | — | — | — | — | Sem dados | — | — | BETS existe no CoinGecko mas sem preço atual. ZTG não aparece na pesquisa. Para Hedgehog, Myriad e PredX não encontrei token no CoinGecko. |

## B) Ferramentas de pesquisa, analytics e agentes de IA para prediction markets (os comparáveis mais próximos)

| Projeto | Ticker | Chain | Produto | Tipo de lançamento | Data | MC hoje | ATH (data) · Δ ATH | Utilidade | Nota |
|---|---|---|---|---|---|---|---|---|---|
| Numerai | NMR | Ethereum | Hedge fund que agrega previsões de cientistas de dados | Airdrop a data scientists (2017) [n/v] | 2017 | **$107,2M** | $93,15 (mai 2021) · −84% | **Staking nas próprias previsões** (perde-se o stake se o modelo falhar) | O modelo "investigação + stake" que mais durou. |
| **Sportstensor / Almanac** (subnet 41 da Bittensor) | **SN41** | Bittensor | **Rede de IA para previsões desportivas**; terminal que envia ordens para o Polymarket | Token alpha de subnet (emissões, sem venda) [n/v] | 2025 | **$10,7M** | $16,73 (2 nov 2025) · −87% | Emissões pagas a mineradores conforme a performance (ROI) | **O comparável mais próximo do No Predictions**: desporto + Polymarket + investigação. |
| Olas (Olas Predict) | OLAS | Ethereum + várias | Rede de agentes, incluindo agentes que apostam em prediction markets | [n/v] | 2023 | $13,2M | $8,47 (2 jan 2024) · −99,5% | Staking de agentes | Produto real; o token mesmo assim caiu −99%. |
| aixbt | AIXBT | Base (+ETH/SOL) | Agente de IA de market intelligence + terminal | Bonding curve da Virtuals (estilo fair launch) | nov 2024 | **$20,6M** | $0,9426 (16 jan 2025) ≈ $943M [est.] · −97,8% | **Acesso ao terminal exige ter 600k AIXBT** | O melhor exemplo de investigação com acesso pago em token. |
| Precog (subnet 55 da Bittensor) | SN55 | Bittensor | Previsão do preço do BTC | Subnet alpha | 2025 | $3,34M | $3,06 (jun 2025) · −80% | Emissões | Não é desporto. |
| **Billy Bets** | $BILLY | Base (migrado de Solana) | **Agente de IA de apostas desportivas** (usa Sportstensor + Sportsdata.io) | Virtuals + financiamento da Coinbase Ventures | 2025 | n/d (FDV **$98,8k**) | $0,00706 (29 jul 2025) ≈ $10,6M FDV [est.] · −99,1% | Acesso premium ao terminal, estratégias automáticas, vaults | Desporto + IA, com apoio de VC, e mesmo assim −99%. |
| **PolyAgent** | POLYAGENT | Solana (**pump.fun**) | "AI powered insights on Polymarket" | Fair launch no pump.fun | 2025 | **$6,5k** | $0,002137 (13 set 2025) ≈ $2,1M [est.] · −99,7% | Nenhuma documentada | Lançado pelo dev, como seria o caso do David. Serve de aviso. |
| PolyTrader by Virtuals | POLY | Base | Agente de IA que negoceia no Polymarket | Virtuals | jan 2025 | $73k | $0,04199 (5 jan 2025) ≈ $42M [est.] · −99,8% | [n/v] | Volume quase nulo. |
| Staicy Sport | SPORT | Base (CreatorBid) | Agente de IA de previsões e comentário desportivo | CreatorBid | 2025 | **$5,2k** | $0,06956 (17 fev 2025) ≈ $1,46M [est.] · −99,6% | [n/v] | IA desportiva com token de agente, e colapsou. |

## C) Tokens de ferramentas de trading (bots, scanners, terminais)

| Projeto | Ticker | Chain | Produto | Tipo de lançamento | Data | MC hoje | ATH (data) · Δ ATH | Utilidade | Nota |
|---|---|---|---|---|---|---|---|---|---|
| Banana Gun | BANANA | Ethereum + Solana | Bot de sniping no Telegram | Fair launch na Uniswap. O 1.º lançamento teve um bug; relançado a 14 set 2023 com airdrop de compensação | set 2023 | **$15,6M** (FDV $32,7M) | $78,62 (19 jul 2024) · −95% | **40% das fees do bot vão para os holders** (mínimo 50 BANANA) | Produto com receita real; o token continua vivo 3 anos depois. |
| Unibot | UNIBOT | Ethereum | Bot de Telegram para a Uniswap | Fair launch, 1M de oferta, com taxa | 17 mai 2023 | $776k | $236,98 (16 ago 2023) · −99,7% | Partilha de receita (40% das fees) | Exploit a 31 out 2023 (~$560–650k). Perdeu a guerra dos bots. |
| Bubblemaps | BMT | Solana + BNB | **Visualização de clusters de carteiras** (o produto mais parecido com o CrawlScan) | VC + Binance HODLer Airdrop | 18 mar 2025 | **$13,7M** (FDV $18,5M) | $0,3173 (18 mar 2025, dia da listagem) · −94% | Votação no Intel Desk, acesso a funcionalidades da V2 | Lançamento com Binance. Nada a ver com um fair launch. |
| **CrawlScan** | CrawlScan | Robinhood Chain | Scanner de holders/"operadores" | Fair launch no Pons v2 (bonding curve) | 4 out 2026 | **$203k** (FDV $221k; liquidez $12,2k) | — (3 dias de vida) | Sorteio para holders + burns; premium planeado | O caso de estudo. Dados do GeckoTerminal às 20:53 CEST. |
| aixbt / PolyAgent / PNP | — | — | ver B e A | — | — | — | — | — | Também contam como "ferramenta + token". |
| Maestro, BonkBot, Photon, Axiom, GMGN, Trojan, Cielo | — | — | Bots e terminais | **Sem token próprio** no CoinGecko (pesquisa de 7 out) | — | n/a | — | — | Os maiores terminais e bots ganham dinheiro com fees sem precisar de token. Nos casos de Axiom e Photon, a pesquisa só devolveu tokens sem relação. |

---

## Conclusões para o No Predictions
1. **Um token de ferramenta lançado pelo dev, sem VC, fica hoje tipicamente entre ~$5k e ~$300k de MC.** Alguns tiveram picos de $1–10M e depois caíram −97% a −99,8%:
   - PolyAgent: ≈$2,1M → $6,5k
   - Staicy Sport: ≈$1,5M → $5k
   - PNP: ≈$4,8M → $130k
   - CrawlScan: $203k ao fim de 3 dias

   O ATH conta pouco; o que interessa é onde o token assenta passados 3 a 12 meses.
2. **Os protocolos de prediction markets com VC e airdrop estão hoje nos $3–11M de MC** (Opinion, Limitless, Sport.fun), com FDV 5 a 8× acima do MC. Quase todos atingiram o máximo no dia do TGE e caíram −84% a −94%. Volume enorme não segura o preço: a Opinion teve $23,4B de volume nocional e o volume caiu 84% quando acabaram os incentivos.
3. **O que segurou valor foi receita real ligada ao token**, seja buyback/burn, partilha de receita ou staking nas previsões:
   - OVER, da Overtime (desporto): só −44% desde o ATH, com colateral + buyback & burn
   - BANANA: ainda $15,6M ao fim de 3 anos, com 40% das fees para os holders
   - NMR: $107M, com staking nas previsões

   Tokens meme ou de agentes sem receita perderam mais de 99%. Os maiores bots e terminais (Photon, Axiom, GMGN, Maestro) nem sequer têm token.
4. **O acesso pago em token só funciona enquanto o produto está em alta.** O aixbt (600k AIXBT para o terminal) continua com $20,6M, mas caiu −97,8%. Os agentes desportivos (Billy Bets, Staicy, PolyAgent) caíram todos mais de 99%. O Sportstensor (desporto + Polymarket, $10,7M) aguenta porque assenta em emissões da Bittensor e em trading real, não num fair launch.
5. **Implicação (inferência minha, não é um dado):**
   - Para um fair launch do No Predictions, é mais realista planear para uma faixa de **~$50k–$500k de MC** depois da euforia inicial.
   - Para defender valor, convém ligar o token ao uso: acesso a investigação premium e/ou parte das receitas em buyback.
   - Também ajuda ter o produto a gerar receita antes do lançamento (a Sport.fun já era rentável antes do TGE).
   - Fica a lição do SX Bet: um token de um produto desportivo pode simplesmente ser descontinuado.

## Fontes (URLs)
- Market caps: CoinGecko API, `https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=...` (às 20:51 CEST). Páginas: https://www.coingecko.com/en/coins/overtime · /opinion · /limitless-3 · /football-fun · /azuro-protocol · /sportstensor · /aixbt · /banana-gun · /bubblemaps · /polyagent · /billy-bets-by-virtuals-2 · /sports-brackets · /numeraire · /gnosis · /drift-protocol · /autonolas · /unibot · /pnp-exchange · /sports-analyst-ai · /polytrader-by-virtuals
- CrawlScan: https://www.geckoterminal.com/robinhood/tokens/0x19dCb63C4d2F29A6f077F094a4f858fC790145e1
- Polymarket sem token: https://cryptobriefing.com/polymarket-onchain-asset-programmable-utility-token2049/ · https://decrypt.co/345854/polymarket-exec-confirms-token-airdrop-after-reenters-us-market
- Hack da Drift: https://www.chainalysis.com/blog/lessons-from-the-drift-hack/ · https://www.theblock.co/news/ecosystems/2026-10-02-drift-opens-exploit-recovery-claims-initial-payouts-just-over-1-user-losses-417581
- Overtime OVER: https://chainwire.org/2025/04/02/overtime-launches-over-token-and-full-account-abstraction-ux/ · https://docs.overtime.io/learn-about-overtime/overtime-amm-and-liquidity-mechanics
- Opinion OPN: https://app.blockworks.com/report/opinion-opn-token-generation-event · https://www.binance.info/en/support/announcement/detail/d5810276f8c44fa7ade2b93746cd6a0a
- Limitless LMTS: https://bsc.news/post/limitless-lmts-token-launch · https://www.cryptotimes.io/2025/10/22/limitless-debuts-lmts-token-amid-multi-chain-expansion/
- Sport.fun FUN: https://blockworks.com/token-transparency/filing/sport-dot-fun · https://docs.sport.fun/usdfun-token/whitepaper/tokenomics
- Azuro: https://www.coingecko.com/en/coins/azuro-protocol
- Fim do token SX: https://blog.sx.bet/news/sunsetting-the-sx-token-and-new-infrastructure-plans/
- PNP: https://phemex.com/news/article/pnp-exchange-migrates-pnp-token-to-pumpfun-for-prediction-market-expansion-34327 · https://docs.pnp.exchange/
- Sports Brackets: https://sportsbrackets.net/2025/06/30/sports-brackets-on-sol-brackets/ · https://migrate.sportsbrackets.net/
- Sportstensor: https://sportstensor.io/docs/overview · https://github.com/sportstensor/sn41
- Billy Bets: https://docs.billybets.ai/ · https://www.prnewswire.com/news-releases/billy-bets-raises-financing-from-coinbase-ventures-to-disrupt-250-billion-market-with-ai-for-sports-prediction-markets-302546782.html
- aixbt: https://crypto.com/en/university/what-is-aixbt-by-virtuals
- Bubblemaps: https://blog.bubblemaps.io/its-official-bmt/ · https://wiki.bubblemaps.io/bmt/tokenomics
- Banana Gun: https://thedefiant.io/news/defi/hyped-banana-token-launch-wrecked-by-contract-bug · https://solanacompass.com/projects/banana-gun
- Unibot: https://crypto.com/en/university/what-is-unibot · https://forklog.com/en/unknown-attacker-hacked-unibot-telegram-bot/
