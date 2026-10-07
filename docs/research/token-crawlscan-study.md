# Estudo de caso: como foi lançado o token CrawlScan
*Pesquisa de 7 out 2026, cerca das 19:55–20:15 (hora de Amesterdão, CEST). Serve de referência para um possível token do No Predictions.*

Legenda: **[V]** verificado on-chain, por API ou no post original · **[A]** afirmado pelo criador e não verificado · **[I]** inferência minha

---

## 1. Factos-chave do lançamento

| Item | Valor | Fonte |
|---|---|---|
| Chain | **Robinhood Chain** (L2 Arbitrum Orbit, chain id 4663) | [V] RPC, DexScreener, GeckoTerminal |
| Contrato | `0x19dCb63C4d2F29A6f077F094a4f858fC790145e1` | [V] |
| Nome/ticker | CrawlScan / CrawlScan, 18 decimais | [V] |
| Supply | 1.000.000.000 fixo, sem mint nem freeze | [V] totalSupply; GT: mint/freeze null, honeypot=false |
| Launchpad | **Pons v2** (pons.family): bonding curve que gradua para um pool Uniswap v4 com liquidez bloqueada para sempre | [V] registo `getLaunchedToken` na factory Pons |
| Tipo de lançamento | Fair launch na curva, sem presale. O supply todo foi cunhado para a curva | [V] 1.º Transfer: mint de 1B para a curva `0x7577…e9dd` |
| Criação | **4 out 2026, 23:49:38 CEST** (bloco 80.268.482) | [V] |
| Quem fez o deploy | `0x58ea…2e03`, que **não é** o Punisher. Segundo ele, foi uma "equipa de devs" que o contactou | [V] deployer no registo e em getTokenInfo · [A] a história |
| Destinatário das creator fees | `0xecef3923905c2aaaa6f40e9f516aa524d8c49b49`, que o site do CrawlScan apresenta como a "dev wallet" | [V] registo Pons + crawlscan.fun/api/rewards/status |
| Taxas por trade | **2% no total**: 1% de fee base (30% para a Pons, 70% para o criador) + **1% de creator tax** (tudo para o criador). Buyback da Pons desligado | [V] `feeBps=100`, `creatorTaxBps=100`, `buybackEnabled=false`, policy 3000 bps para o protocolo |
| Graduação | **6 out, 05:02:29 CEST**, ao atingir 4,2 ETH na curva → pool Uniswap v4 `0x835f8d…7892` | [V] GT launchpad_details + registo (phase 2) |
| Liquidez | Bloqueada para sempre pelo design da Pons, sem forma de a retirar. Às ~19:55 de 7 out: ~$33–39k (≈7,6 ETH + ~112M tokens) | [V] docs Pons; GT/DexScreener |
| Supply excedente bloqueado | ~81,6M (8,2%) num contrato da Pons (`0x2674…4952`, provavelmente o Launch Locker) | [V] saldo · [I] papel exato do contrato |
| Queimado | 2.000.001 tokens (0,2%) em `0x…dEaD`, em 4 burns feitos pela dev wallet | [V] saldo + API do site |
| Auditoria | Nenhuma ao token. Segundo os próprios docs, os contratos v2 da Pons ainda não têm auditoria concluída | [V] docs Pons |
| Market cap | ~$160–190k às ~19:55 CEST de 7 out (no screenshot: $177k, $0,000193). Liquidez fina para este valor | [V] GT/DexScreener |
| Holders | ~650–670 | [V] GT; contagem própria: 652 com saldo |
| Listagens | DexScreener "Enhanced Token Info" pago (5 out ~17:16 CEST, MC $22k); verificação no GeckoTerminal com Fast Pass (6 out); página no CoinGecko (`crawlscan-2`) | [V] posts + GT `gt_verified: true` |

### O que aconteceu no lançamento (on-chain)
- **Bloco 0 (23:49:38):** o deployer lança o token e compra no mesmo tx, através do router launch-and-buy da Pons: **30M tokens (3%) por ~0,053 ETH**. [V]
- **Bloco +1 (~0,1 s depois):** um único tx, a partir de `0x421c…` através do contrato `0x14b9…`, compra **300M tokens (30% do supply) para 3 carteiras, com ~0,79 ETH**. Pagaram só 1% + 1% de taxa, **sem a anti-snipe tax** (que começa em 99%). Na Pons, isso só acontece a carteiras isentas que o criador indica no momento da criação. [V] · [I] daqui infiro que este bundle era da equipa do deployer.
- **Blocos +34 a +44 (~4–5 s depois):** as 3 carteiras do bundle vendem os 300M através de um router e recebem **~1,82 ETH**. Lucro de cerca de **+1,03 ETH** em segundos. [V]
- **~47 min depois (~00:37 CEST de 5 out):** o deployer vende os seus 30M por ~0,056 ETH. [V] O post do Punisher "$CRAWLSCAN IS LIVE" saiu às **00:05 CEST**, por isso esta venda bate com o "rugaram logo a seguir ao meu post" que ele conta. [V] horas · [A] narrativa
- Na curva, o market cap passou de ~$4,8k a um máximo de ~$30k na 1.ª hora e caiu para ~$4,9k por volta da 01:00 CEST. Ficou entre ~$10–12k durante a noite e desceu a ~$6k às 13:00 de 5 out. [V] OHLCV do GT

### A recuperação
- 5 out, 16:53 CEST: o Punisher publica um post a explicar o golpe ("Let me explain everything…"). Diz que comprou "no topo" a ~$30k de MC e que não vendeu. Paga a DexScreener com as fees. [A] as compras dele (não identifiquei a carteira) · [V] o pagamento da Dex
- A seguir, pequenos KOLs (2–3,5k seguidores) fazem calls; o MC na curva sobe para ~$45k entre as 19:00 e as 20:00 CEST. O token gradua às 05:02 de 6 out. Depois disso, os máximos foram de ~$189k (6 out) e ~$228k (7 out ao fim da tarde). [V] GT

---

## 2. Supply e distribuição (calculado a partir de 24.673 Transfers, às ~20:00 CEST de 7 out)
- Infraestrutura: PoolManager do Uniswap v4 com 11,3%, locker da Pons com 8,2% e dead com 0,2%. **Float ≈ 803M.** [V]
- Dev/equipa: o deployer tem **0%** (vendeu tudo). A dev wallet do projeto (`0xecef…`) tem ~4,47M tokens (0,45%) e ~3,28 ETH (≈ $8–9k), juntados com as creator fees. [V]
- Concentração, fora da infraestrutura: os top 5 têm 15,6% do float, os top 10 têm 28,9%, os top 20 têm 44,9% e os top 50 têm 66,4%. Os maiores holders individuais ficam entre 2,0% e 2,9% cada. [V]
- GeckoTerminal, contando com a infraestrutura: top 10 com 39%, 11–30 com 23,6%, 31–50 com 10%, resto com 27,4%. [V]
- Os maiores holders compraram através de routers e bots de trading, em horas diferentes. Pelos tokens, não vejo nenhum cluster óbvio de uma só carteira financiadora, mas **não analisei o financiamento em ETH**. [V] parcial / [I]
- Bundle e snipers: o bundle de 30% do bloco +1 saiu por completo em segundos. Das ~87 carteiras que entraram nos primeiros ~5 min, as que verifiquei já venderam tudo. [V]
- O hoodtape mostra que os 5 maiores holders "fomo" têm 33% do que os fomo wallets detêm. Contagem desde o início: 395 carteiras fecharam com lucro e 589 com prejuízo. [V] hoodtape (métrica deles)

---

## 3. Relação entre o produto e o token
- **A ferramenta está ativa e é gratuita** [V]: crawlscan.fun (o `/health` responde ok, há feed de scans recentes, ~12 scans em 2,5 min no momento em que verifiquei) e há um bot de Telegram. Analisa os top holders de tokens da Robinhood Chain e da Solana: carteiras ligadas, carteiras virgens, snipers, comportamento do deployer, score de 0 a 100 e aviso de rug.
- **É open source** [V]: github.com/0xPunisher/crawlscan, licença MIT, Python, repo criado a 4 out às 16:22 CEST. Os comentários do código estão em russo (só uma observação).
- **O token não dá acesso ao scanner.** O próprio Punisher diz que o scan "fica grátis para sempre". [A]/[V] no código não há gate
- **O que existe hoje para quem tem o token** [V] no código e na API:
  1. **Sorteio diário:** um holder aleatório ganha **10% das creator fees do dia**, convertidas em tokens. A probabilidade é proporcional ao saldo médio do dia. O vencedor sai do hash de um bloco e pode ser verificado no site. Até agora houve 1 sorteio (6 out): 1.712 participantes, prémio de 4M tokens enviados a `0x2dc4…`.
  2. **Burns a cada 12h** (10:00 e 22:00 UTC), com tokens comprados ou detidos pela dev wallet. O total até agora é 0,2% do supply, o que é simbólico.
- **Prometido mas ainda não entregue:** camada premium para holders (memória de operadores, launch radar, wallet profiler, mais slots de alertas, "execução mais rápida"), extensão de browser, "grandes parcerias", airdrop para quem promove o projeto. [A]
- **Monetização paralela:** os botões "Trade" do site levam para o Axiom com referral `@crawlscan` (`/api/config`). [V] o link · [I] que gera receita de referral
- **Resumo:** é sobretudo uma **memecoin colada a um produto real e grátis**. As fees servem de orçamento de desenvolvimento e de marketing. A ligação ao token é feita por mecânicas de recompensa (sorteio e burns), não por utilidade necessária. O próprio Punisher escreveu que "o token foi lançado por coincidência".

---

## 4. Playbook de marketing (cronologia em CEST)
| Quando | O quê | Alcance |
|---|---|---|
| 4 out 17:34 | "I BUILT AND OPEN SOURCED THE FIRST WEB CRAWLER FOR ROBINHOOD MEMECOINS", com repo | 11,1k views, 63 likes |
| 4 out 21:20 | "CRAWLSCAN… IS LIVE AND FREE" | 9,1k views |
| 4 out 23:49 | *O token é criado por terceiros* | |
| 5 out 00:05 | "$CRAWLSCAN IS LIVE", CA + "o único token oficial" + roadmap para holders | 3,1k |
| 5 out 00:24 | "Para onde vão as fees": dev, extensão, bot de TG, buybacks | 1,2k |
| 5 out 16:53 | **Post de transparência sobre o rug** ("fui enganado, não vendi") | 19,1k |
| 5 out 17:16 | DexScreener pago, com bots de alerta "Dex paid" | |
| 5 out 16:22–19:00 | Calls de micro-KOLs (@deravertt "in at 11k", @Stackerr777 "doxxed dev ex-polymarket" (não verificado), @BarnabusBull) | centenas de views cada |
| 5 out 19:55 | Suporte para Solana ("em <24h") | 10,4k |
| 6 out 03:36 | **Mecânicas para holders**: burns a cada 12h + sorteio diário verificável | 16,1k |
| 6 out 07:43 | "Ultimate guide to trade memecoins SAFE" | 20,4k |
| 6 out 08:31 | Verificação no GeckoTerminal + Fast Pass ("nunca fiz isto") | 3,6k |
| 6 out 11:22 | "Passámos $100k de MC", "BIG partnership is coming", "2 ofertas de collabs" | 4,2k |
| 6 out 14:58 | Bot de Telegram em produção | 6,8k |
| 6 out 19:26 | **X Article** "I built CrawlScan. Here's how this will improve your trading." | 14,1k views, 96 likes, 25 RT, 30 respostas |
| 6 out 22:44 | Funcionalidade "rug early warning" | 5,5k |
| 7 out 00:23 | 1.º vencedor do sorteio + 2.º burn | 4,4k |
| 7 out 15:56 | Conta oficial @CrawlScan_; "os melhores posts são retweetados e recompensados" + airdrop | 1k |
| 7 out 19:02 | Alertas no bot de TG (3 tokens por conta; mais slots "para holders") | |

**Padrões:** ritmo de "ship diário" (várias funcionalidades por dia), sempre com o CA no post. Narrativa de underdog ("fui rugado e continuei a construir", "no sleep, only build", "you are early"). Muitas respostas a pequenas contas. Não há Telegram de comunidade; ele recusa abrir um para "não dividir". O resto da promoção veio de micro-KOLs, bots de alertas "Dex paid", posts tipo "vote on fomo" e prova social ("@phantom likes…"). Não encontrei KOLs grandes pagos. [V] na amostra pequena que vi (15 posts de outras contas)

Contexto do ecossistema [V]: segundo um bot de stats da Pons, numa só hora de 5 out foram lançados 218 tokens e **nenhum** graduou. O CrawlScan era o que estava mais perto, com 73%.

---

## 5. Red flags e riscos
1. **Lançamento por terceiros com bundle:** 30% do supply comprado no bloco +1, sem anti-snipe tax (carteiras isentas na criação), e despejado em ~5 s. Quem comprou cedo perdeu cerca de 80% na 1.ª hora. [V]
2. **2% de custo em cada trade** (1% de fee + 1% de creator tax), a favor da dev wallet. [V]
3. **Hype e promessas:** "sky is the limit", "$1M MC", "BIG partnership", "every token left becomes more valuable", "you have no idea how early you are". O premium para holders continua por entregar. [V] posts
4. **Burns simbólicos** (0,2%) apresentados como criadores de valor. **Sorteio** pago com fees. [V]
5. **Liquidez fina** (~$35k) para ~$170k de MC, com velas de ±20–50% por hora. [V]
6. **Concentração:** os top 20 holders têm ~45% do float. [V]
7. **Imitações e fraude:** a DexScreener mostra pelo menos 8 outros tokens "CrawlScan" (Robinhood, Base, BSC, Solana pump.fun, @CrawlScanRBH) e há posts de phishing do tipo "claim the $CRAWLSCAN airdrop, connect wallet". [V]
8. **Contratos da Pons v2 sem auditoria concluída** (dizem-no os próprios docs). Isto é risco de plataforma. [V]
9. **Confiança numa só pessoa:** dev pseudónimo (conta criada em mar 2025, 6,7k seguidores, apresenta-se como "Prediction markets developer, frauds investigator, @polymarket maxi"). Antes, publicava sobre bots de arbitragem no Polymarket. [V] perfil
10. **Possível referral escondido** nos botões de trade (Axiom). [V] o link / [I] a receita

---

## 6. Lições para um token do No Predictions
**Copiar:**
- **Produto primeiro, token depois.** O CrawlScan sobreviveu ao rug porque o produto existia, era grátis e útil, e foi melhorando todos os dias em público. Um token sem produto não teria recuperado.
- **Lançar tu próprio, numa fair launch** (supply todo na curva, liquidez bloqueada automaticamente). **Nunca deixar terceiros lançar "o teu" token.** Publica tu o CA oficial e avisa que qualquer outro é falso.
- **Transparência on-chain:** carteira de fees pública, gastos publicados, sorteios e burns verificáveis num painel no site.
- **Ritmo de shipping visível**, com um post por funcionalidade e um X Article longo a explicar o produto. Responder a toda a gente.
- **Ligação ao produto que faça sentido:** por exemplo, funcionalidades premium do No Predictions (alertas de odds/mercados, histórico de previsões, dashboard multi-desporto) para holders, mantendo o núcleo grátis.
- Pagar DexScreener e GeckoTerminal só depois de haver tração.

**Evitar:**
- Bundles e snipers: não isentes carteiras da anti-snipe tax e não compres com várias carteiras.
- Promessas de preço ou "parcerias" vagas. Burns simbólicos vendidos como valor.
- Creator tax extra (o CrawlScan tem 1% adicional). Fica pela fee base, ou explica bem para onde vai cada cêntimo.
- Mecânicas de sorteio ou lotaria pagas com fees, sobretudo num produto ligado a previsões e apostas desportivas.
- Comprar o teu próprio token com dinheiro que não podes perder, ou criar dependência do preço.

**Passos e custo aproximado (se fosse na Pons/Robinhood Chain, como o CrawlScan):**
1. Ter o produto ativo e com uso real (nopredictions.com), mais repo/demo e um post explicativo.
2. Carteira EVM dedicada ao projeto (fees), separada da pessoal.
3. Lançamento na Pons v2: **0,0005 ETH** de launch fee (~$1,3) + gas (frações de cêntimo). Supply de 1B, graduação aos 4,2 ETH, LP bloqueada automaticamente. Atenção: os docs da Pons dizem que o lançamento público v2 pode estar **restrito a whitelist** (`canLaunch`), por isso é preciso confirmar. Alternativas noutras chains: pump.fun (Solana), Clanker/Zora (Base). Não as comparei a fundo.
4. Compra inicial própria opcional (o deployer do CrawlScan comprou 3% por ~0,053 ETH). É mais limpo não comprar, ou comprar pouco e anunciá-lo.
5. Perfil pago na DexScreener: **$299** (preço anunciado em ago/set 2026, confirmar na checkout). Fast Pass no GeckoTerminal: **$199** por pedido (opcional; a verificação normal é gratuita mas mais lenta).
6. Arte (logo e banner), site com o CA oficial e painel de transparência: o teu tempo, ou ~$100–300 se encomendares (estimativa minha).
7. **Orçamento mínimo realista: ~$500–800** (Dex + Gecko + arte), sem contar KOLs. O retorno depende do volume: o criador fica com ~1,7% do volume no esquema do CrawlScan (com creator tax) ou ~0,7% (só a fee base). Em ~3 dias, a dev wallet do CrawlScan juntou ~3,3 ETH (≈ $8–9k) [V saldo] com cerca de $600k de volume em 7 dias segundo a OpenSea [V].

---

## Fontes principais
- DexScreener (par principal): https://dexscreener.com/robinhood/0x835f8dfd0065bb0538b3944f286baafd70a779d433cb76a8a27e6ad0a4517892
- GeckoTerminal: https://www.geckoterminal.com/robinhood/pools/0x835f8dfd0065bb0538b3944f286baafd70a779d433cb76a8a27e6ad0a4517892
- CoinGecko: https://www.coingecko.com/en/coins/crawlscan-2
- Blockscout: https://robinhoodchain.blockscout.com/token/0x19dcb63c4d2f29a6f077f094a4f858fc790145e1
- OpenSea (token): https://opensea.io/token/robinhood/0x19dcb63c4d2f29a6f077f094a4f858fc790145e1
- hoodtape (fluxos e holders): https://hoodtape.com/token/0x19dcb63c4d2f29a6f077f094a4f858fc790145e1
- Site e API: https://crawlscan.fun · https://crawlscan.fun/api/rewards/status · https://crawlscan.fun/api/rewards/history
- Repo: https://github.com/0xPunisher/crawlscan
- Docs da Pons v2: https://docs.ponsfamily.com/v2 · Bitquery Pons API: https://docs.bitquery.io/docs/blockchain/robinhood/pons-api/
- Posts no X: perfil https://x.com/0x_Punisher · open source https://x.com/0x_Punisher/status/2106769909183099009 · "IS LIVE" https://x.com/0x_Punisher/status/2106868334243250425 · explicação do rug https://x.com/0x_Punisher/status/2107121906491019617 · mecânicas para holders https://x.com/0x_Punisher/status/2107283863122636824 · X Article https://x.com/0x_Punisher/status/2107522921090400475 · alertas https://x.com/0x_Punisher/status/2107879363022557533
- Calls de KOLs: https://x.com/deravertt/status/2107114161436078086 · https://x.com/Stackerr777/status/2107128922681094237 · stats da Pons https://x.com/gradgatebot/status/2107138820839813435
- Preços: DexScreener ETI https://dxttools.trade/dexscreener/dex-paid · GeckoTerminal Fast Pass https://support.coingecko.com/hc/en-us/articles/45530595319449-What-is-GeckoTerminal-Fast-Pass

## Método
- Leituras on-chain através do RPC público da Robinhood Chain (factory Pons `getLaunchedToken`, `feeBps`/`creatorTaxBps`, saldos). Todos os eventos Transfer do token (24.673) e eventos da curva (CurveBuy/CurveSell) foram analisados com scripts em `/workspace/no-predictions/` (`transfers.json`, `curve_logs.json`, `rpc.py`).
- X API: perfil, 68 posts do @0x_Punisher (1–7 out) e uma pesquisa de 15 posts de terceiros. Custo de ~$0,42 em créditos; não publiquei, não fiz like e não contactei ninguém.
- Não identifiquei as carteiras pessoais do Punisher (as compras dele "a $30k" ficam por verificar) nem analisei o financiamento em ETH dos maiores holders.
