# Custos de execução para lançar um token: Solana vs Robinhood Chain
*7 out 2026. Só execução: sem marketing, sem listagens pagas e sem arte.*

**Preços usados (CoinGecko API, 7 out 2026, 20:03 CEST):** SOL = **€104,01** ($116,49) · ETH = **€2.281,01** ($2.554,74). Fonte: https://api.coingecko.com/api/v3/simple/price?ids=solana,ethereum&vs_currencies=eur,usd

Legenda: **[V]** verificado em docs oficiais ou on-chain hoje · **[S]** fonte secundária · **[E]** estimativa ou cálculo meu · **[?]** não verificado

---

## Tabela comparativa

| Chain | Caminho | Custo único (nativo) | ≈ EUR | Liquidez que tens de pôr | Taxas contínuas (por trade) |
|---|---|---|---|---|---|
| Solana | **A · pump.fun** | 0 SOL para criar [V]. Gas/rent da rede baixos, montante exato [?]. A taxa de graduação de 0,015 SOL sai da curva [V] | ~€0–2 | **0**. Os compradores enchem a curva; gradua com ~85 SOL [S/E] | 1,25% na curva (0,30% para o criador) [V]. Depois da graduação, no PumpSwap, 1,25% até 420 SOL de MC e depois 1,20%→0,30% (o criador fica com 0,95% entre 420 e 1.470 SOL de MC) [V] |
| Solana | A · Raydium LaunchLab (base do LetsBonk) | ~0,115 SOL (0,1 SOL de taxa LaunchLab + ~0,015 de rent) [V docs Raydium] | ~€12 | 0. Gradua aos 85 SOL (JustSendit) ou num alvo próprio de ≥24 SOL [V] | 1% compra/venda por defeito (lp:criador:protocolo 60:20:20) + taxa da plataforma até 5% (a do LetsBonk [?]) [V]. Depois da graduação: CPMM 0,25% [V] |
| Solana | A · Meteora DBC | Depende do frontend. Taxa de criação configurável entre 0,001 e 100 SOL, mais 0,2% de taxa de migração [V docs Meteora] | [?] | 0 | Configurável por plataforma [V] |
| Solana | **B · Manual** (SPL + Raydium CPMM) | Mint 0,00107 + metadata 0,0041 (rent) + **0,01 Metaplex** + ATA 0,0015 + revogar mint/freeze (~0,00001) + **pool CPMM ~0,19–0,2 SOL** (0,15 de taxa + rent) ≈ **0,22 SOL** [V rent via RPC hoje; V docs Raydium/Metaplex] | **~€23** | **A tua**: os dois lados do pool (SOL + tokens). Ver a secção de liquidez | 0,25% (tier por defeito) para os LPs. Creator fee opcional [V] |
| Robinhood | **A · Pons v2** | **0,0005 ETH** de launch fee [V on-chain `launchFee()`] + gas ~0,00008 ETH [V tx medido] | **~€1,3** | 0. Gradua aos **4,2 ETH** (≈€9,6k) para Uniswap v4 com LP bloqueada para sempre [V] | 1% (70% criador / 30% protocolo) + creator tax opcional de 0–10% [V `maxCreatorTaxBps=1000`] |
| Robinhood | **A · Pools.trade** (Uniswap Labs), Instant Launch | **Sem taxa de launchpad**, só gas (< 0,0001 ETH [E]) [V blog Uniswap] | ~€0,2 | 0. Pool v4 com o supply todo single-sided, liquidez bloqueada para sempre [V] | 0,25% de LP fee que se autocompõe na liquidez bloqueada. Creator fee opcional de 0,05% [V] |
| Robinhood | **B · Manual** (ERC-20 + Uniswap v4) | Deploy + criação do pool + LP: tudo < 0,0002 ETH em gas (gas a ~0,02 gwei; um launch Pons que fez deploy de 2 contratos custou 0,00008 ETH; criar o pool v4 e mintar a LP na graduação custou 0,000023 ETH) [V txs medidos / E para o ERC-20 simples] | **< €0,50** | **A tua** (ETH + tokens) | Fee tier escolhido no pool (ex.: 0,25–1%) para os LPs [E] |

**Bloquear ou queimar a LP (caminho B):** em Solana podes queimar os LP tokens do CPMM com uma transação normal (só fee de rede), ou usar o Burn & Earn da Raydium. Na Robinhood podes enviar o NFT da posição v4 para um endereço morto (só gas). Não verifiquei se lockers de terceiros (UNCX, Team Finance) já suportam a Robinhood Chain [?]. Nos launchpads (A), a liquidez fica bloqueada automaticamente [V].

---

## Notas por chain

### Solana
- **pump.fun** [V https://pump.fun/docs/fees, atualizado a 20 mai 2026]: criar custa 0 SOL ou 0 USDC; a graduação para o PumpSwap custa 0,015 SOL. Desde 21 mai 2026 é possível emparelhar com USDC. O pool canónico é o único que paga creator fees. Os pools não canónicos têm 0,3% de fee e 0% para o criador.
- **Graduação no pump.fun** [S softatjeh/deepwiki, a partir das constantes publicadas]: quando os 793,1M tokens da curva se esgotam, ou seja, ~85 SOL de compras líquidas. O MC à graduação ronda os **~411 SOL (≈ €42,7k hoje)**. O pool fica com **~85 SOL (≈ €8,8k) + 206,9M tokens**, e os LP tokens são queimados. O site oficial só diz que gradua "ao atingir o graduation threshold" [V https://pump.fun/docs/bonding-curve].
- **Raydium LaunchLab** [V https://docs.raydium.io/reference/fee-comparison e https://docs.raydium.io/user-flows/creating-a-launchlab-token]: no lançamento, ~0,115 SOL. A graduação para CPMM custa ~0,04 SOL, pagos das reservas da curva. A taxa da plataforma (LetsBonk e outras) tem um teto de 500 bps desde 26 ago 2026 [V https://docs.raydium.io/products/launchlab/platform-config]. O valor atual do LetsBonk está [?].
- **Manual**, com rent lida hoje por `getMinimumBalanceForRentExemption` na mainnet [V]:
  - mint (82 B): 0,0010668 SOL
  - metadata (679 B): 0,0040996 SOL, mais a taxa Metaplex de criação de 0,01 SOL [V https://github.com/metaplex-foundation/disclosures/blob/main/protocol-fees.md]
  - token account (165 B): 0,0014884 SOL
  - pool CPMM: 0,15 SOL de taxa [V https://docs.raydium.io/ray/protocol-fees], ~0,19 SOL no total, ~0,2 SOL na UI [V https://docs.raydium.io/user-flows/create-cpmm-pool]. O CPMM não precisa de mercado OpenBook. Um pool CLMM não paga taxa de criação (~0,061 SOL de rent), mas obriga a gerir ranges [V].

### Robinhood Chain
- **Pons v2** [V docs https://docs.ponsfamily.com/v2 + leituras on-chain hoje]:
  - `launchFee` = 0,0005 ETH;
  - `launchEnabled` = true e `canLaunch(endereço aleatório)` = **true**. Ou seja, **hoje o lançamento é público**, apesar de os docs ainda falarem em whitelist;
  - fee base de 1%, creator tax de 0–10%;
  - anti-snipe de 99% a cair para 0 em ~5 s;
  - graduação aos 4,2 ETH para Uniswap v4, com a LP bloqueada para sempre.
  - Os contratos v2 ainda não têm auditoria publicada [V docs].
- **Pools.trade** [V https://blog.uniswap.org/pools-trade-a-new-way-to-launch-on-robinhood-chain + https://developers.uniswap.org/llms.mdx/docs/liquidity/liquidity-launchpad/concepts/instant-launch]:
  - Instant Launch: supply de 1B posto todo num pool v4 ETH/token, LP fee de 0,25%, posição detida para sempre pelo FeeSplitter. Para lançar, basta o token.
  - Crowd Launch: janela de 4h com bids TWAP; se não chegar a $10k de FDV, os bids são reembolsados.
  - O criador pode comprar no mesmo bloco do lançamento.
- **Outros** [S, não verificados]: hood.fun (curva → Uniswap v3 bloqueado, taxas não publicadas), o1 Launchpad (0,001 ETH segundo terceiros), LONG/PAIR (emparelhados com ações tokenizadas), Bankr, LetsCash. O Noxa parou em julho de 2026.
- **Gas** [V]: `eth_gasPrice` ≈ 0,0213 gwei hoje. Uma transferência (burn) custou 0,0000007 ETH. Na prática, o gas é irrelevante.

---

## Dev buy (opcional)
- Comprar uma pequena % logo no lançamento, **a partir de uma só carteira e anunciado**, é normal e protege dos snipers. O pump.fun, o Pons (`launchAndBuy`) e o Pools.trade permitem comprar no mesmo bloco ou transação.
- **O que pareceu mal no CrawlScan não foi o deployer comprar 3%** (por 0,053 ETH ≈ €121 [V]). Foi um **bundle de 3 carteiras isentas da anti-snipe tax a comprar 30%** e a vender em ~5 s [V].
- **Custo no pump.fun** [E, a partir das constantes da curva: 30 SOL virtuais / 1,073B tokens virtuais, com 1,25% de fee]: 1% ≈ 0,29 SOL (€30) · 2% ≈ 0,58 SOL (€60) · 3% ≈ 0,87 SOL (€91) · 5% ≈ 1,49 SOL (€154).

---

## Liquidez mínima (só relevante no caminho B)
Num pool x·y=k, uma compra de valor *x* contra uma reserva do lado SOL/ETH com valor *R* faz o preço subir ≈ ((R+x)/R)² − 1 [E, matemática constant-product]:

| Reserva do lado SOL/ETH | Compra de €100 | Compra de €500 |
|---|---|---|
| €500 | +44% | +300% |
| €1.000 | +21% | +125% |
| €2.000 | +10% | +56% |
| €5.000 | +4% | +21% |
| €10.000 | +2% | +10% |
| €20.000 | +1% | +5% |

**Referências reais:** um token do pump.fun gradua com ~85 SOL ≈ €8,8k no lado SOL [S/E]. O Pons gradua com 4,2 ETH ≈ €9,6k [V]. O CrawlScan tinha ~$33–39k de liquidez total com ~$170k de MC [V]. O LaunchLab aceita um mínimo de 24 SOL ≈ €2,5k [V].

**Recomendação [E]:**
- **Mínimo absoluto ~€1–2k** no lado SOL/ETH. Abaixo disto, cada compra de €100 mexe o preço 10–20% ou mais, e o token parece um rug ou uma armadilha.
- **Sensato: €5–10k**, ao nível dos pools que saem de uma graduação. Fica com ~2–4% de impacto por cada €100–200.
- **Se queimares a LP, esse capital fica preso para sempre**, porque não o recuperas. É também por isso que é credível. Se não a bloqueares ou queimares, o mercado vê isso como risco de rug.

---

## Recomendação: o lançamento mais barato que faz sentido
1. **Robinhood:** Pools.trade Instant Launch (sem taxa, só gas, liquidez bloqueada, 0,25% de fee) ou Pons v2 (~€1,3, 1% de fee, criador fica com 70%). Nenhum dos dois exige capital próprio.
2. **Solana:** pump.fun (0 SOL para criar, 1,25% de fee, 0,30% para o criador na curva). Não precisa de liquidez própria; gradua com ~85 SOL de compradores.
3. Dev buy opcional de 1–3% a partir de uma só carteira e anunciado (~€30–120). O caminho manual (B) só faz sentido se tiveres **pelo menos €5k** para trancar na liquidez.
