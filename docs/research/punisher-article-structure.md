# Análise da estrutura do X Article de @0x_Punisher
**"I built CrawlScan. Here's how this will improve your trading."** · https://x.com/0x_Punisher/status/2107522921090400475
Publicado a **6 out 2026, 19:26 CEST**. Li o texto completo através do X connector (campo `article.plain_text`), e o texto bruto está guardado em `/workspace/no-predictions/punisher-article-raw.txt`.

**Métricas (API do X, 7 out por volta das 20:35 CEST):** 14.532 impressões · 97 likes · 30 respostas · 26 reposts + 9 quotes · 22 bookmarks.
**Formato:** ~1.570 palavras (~8.900 caracteres). Tem 1 imagem de capa, **11 fotos + 1 vídeo** dentro do artigo e **5 posts embebidos** (anúncio do open source, história do rug, score 88, bot de Telegram, mecânicas de holders).
*Nota: o texto simples não traz a formatação. Os títulos de secção abaixo foram deduzidos das linhas curtas isoladas, e não sei em que ponto do texto aparece cada imagem.*

---

## 1. Estrutura secção a secção

| # | Secção (título tal como aparece) | Função | Evidência (citações curtas) |
|---|---|---|---|
| 0 | **Título** | Prova de autoria ("I built") + benefício para o leitor ("improve your trading") | "I built CrawlScan" · "improve your trading" |
| 1 | **Hook**, sem título | Pega numa crença comum e desmonta-a: uma lista de holders parece segura, mas esconde uma só pessoa | "felt safe" · "It looks like a crowd." · "wallets, not people" · "one exit waiting to happen" |
| 2 | **Problema → a pergunta que importa** | Diz que as outras ferramentas não chegam e põe o produto como resposta | "None of that answers" · "How many real people are behind this token" · "That is the question CrawlScan answers." |
| 3 | **The idea** | Origem: o que o irritava e a regra que definiu para o produto | "Animated spiders… doing nothing" · "crawlers that actually crawl" · "no noise" · "Paste a contract… get a verdict" |
| 4 | **$CrawlScan token story** | Antecipa a objeção sobre o token: o rug feito por terceiros, ele a não vender, o token como recompensa. Embebe a história completa | "I never planned to have a token." · "I didn't sell a single token." · "the point was never the token" |
| 5 | **What CrawlScan actually does** | Explica o produto: sinais verificados em cada carteira → ligações entre carteiras → "operadores" → dump impact → score e linhas vermelhas | "like an analyst" · listas "Did it actually buy…", "Wallets funded from the same source" · "One operator is one real person" · "dump impact" · "flagged as DANGER" |
| 6 | **An example: my own token** | Prova com um caso concreto, usando o próprio token (diz que não quer expor outros) | "not going to put someone else's token on blast" · "$CrawlScan is scored 88." · "the score is alive" |
| 7 | **Why it is different** | Diferenciação em pares "X, not Y" | "Seconds, not research sessions" · "People, not wallets" · "Price impact, not percentages" · "Open source… You don't have to trust me" |
| 8 | **Four releases in under 48 hours** | Prova de ritmo: changelog v1.0–v1.3 | "v1.0… v1.3" · "in about a day and a half" |
| 9 | **CrawlScan in Telegram** | Segundo canal de uso + crescimento viral (adicionar a grupos) | "Add it to your group" · "the whole chat sees… before anyone apes" |
| 10 | **$CrawlScan: burns and holder rewards** | Economia do token: burns, sorteio, como verificar e porquê | "Every 12 hours" · "10% of the creator fees" · "My own wallet is excluded" · "Verify button" · "not to the people who flip it" |
| 11 | **What's next** | Roadmap, com o premium para holders no fim | "Operator memory" · "Wallet profiler" · "Premium features for $CrawlScan holders" |
| 12 | **Scan before you buy** (CTA) | Ação concreta + links + CA, seguido de agradecimento pessoal e FOMO | "make this the last thing you do before you press buy" · links site/bot/GitHub/canal TG · CA · "BIG THANK YOU" · "You are still early." |

---

## 2. Técnicas usadas
- **Tipo de hook:** desmonta uma crença ("parece seguro, mas não é"). São frases curtas, uma por linha, com contraste ("On the chart… / In reality…"). O leitor reconhece-se logo ("Every memecoin trader has…").
- **Enquadramento do problema:** a dor é concreta e financeira (o dump; num post anterior ele escreve "the coin that takes your whole bag"). Termina numa **única pergunta** que o produto responde.
- **História:** duas, curtas. A **origem** (spiders animados que não fazem nada → "crawlers that actually crawl") e o **arco de redenção** do token (enganado → não vendeu → continuou a construir). Conta a história do token cedo (secção 4) para tirar a objeção do caminho antes de explicar o produto.
- **Prova:**
  - exemplo real com o próprio token (score 88);
  - changelog com 4 versões em ~48h;
  - código open source ("you can read how every single rule works");
  - verificação on-chain do sorteio;
  - 11 imagens + 1 vídeo + 5 posts embebidos;
  - prova social sem números ("people who tried it call it a game changer"). Não há métricas de uso no artigo.
- **Como explica o produto:** primeiro a metáfora ("like an analyst"), depois a sequência lógica (sinais por carteira → ligações → operadores → impacto → score), e só depois as regras duras ("red lines"). Traduz o técnico em consequência: "12% of supply means nothing… '-45% if sold' tells you exactly what you are risking".
- **Token:** aparece **3 vezes em blocos próprios**: história (secção 4), mecânicas (secção 10) e premium (roadmap). Também está nos links finais (CA). O produto fica à frente; o token surge como "recompensa", não como investimento. "$CrawlScan" aparece 6 vezes no texto.
- **Tom:** pessoal e na 1.ª pessoa (~31 "I/my/me" contra ~34 "you/your"), direto, sem jargão de marketing, emocional no fim. Algumas gralhas ("usaully", "her your suggestions") tornam-no mais humano.
- **Tamanho e formatação:** ~1.570 palavras, ~11 secções com títulos curtos, muitas linhas de uma frase, listas de bullets e o padrão "X, not Y". Muito visual, com imagem a cada secção ou duas [I].
- **CTA:** uma ação clara ("scan before you buy"), 5 links (site, bot, GitHub, canal TG, CA), um pedido de sugestões e FOMO no fim ("You are still early").

---

## 3. O que funcionou e fraquezas
**O que funcionou**
- **Alcance:** 14,5k impressões, cerca de 2,3× a mediana (~6,3k) dos 17 posts que ele publicou de 1 a 6 out (respostas excluídas; métricas lidas hoje). Só três tiveram mais: o "Ultimate guide" (20,4k), a explicação do rug (19,1k) e o post das mecânicas para holders (16,1k).
- **Interação:** 97 likes (~0,7% das views), 30 respostas, 35 partilhas (26 RT + 9 quotes) e **22 bookmarks**. Os bookmarks são o sinal de "guardar para usar", típico de conteúdo de referência.
- **Porque funcionou [I]:**
  - a dor é universal no público dele (memecoin traders);
  - a pergunta é única e clara;
  - a prova é verificável (open source, on-chain);
  - a narrativa de underdog resolve a objeção "isto é um token de scam?";
  - o ritmo de shipping cria momentum;
  - o produto é grátis, por isso o CTA não tem atrito.

**Fraquezas**
- **Comprido e repetitivo:** o sorteio e os burns repetem quase palavra por palavra o post de 6 out às 03:36 CEST. "Why it is different" repete a secção "does".
- **Prova circular:** o único exemplo é o próprio token, com um score que muda com o tempo.
- **Prova social vaga:** "call it a game changer" sem números de utilizadores ou de scans, nem um caso de rug detetado. Esse caso aparece noutro post, mas não aqui.
- **Promessas sem data** no roadmap ("Premium features… faster execution").
- **O token mistura-se com o produto:** sorteio, burns ("supply gets smaller") e "You are still early" no fim empurram para a especulação e chocam com o "the point was never the token".
- **Gralhas e cópia irregular**, e não se sabe onde ficam as imagens no texto [limitação da leitura].

---

## 4. Template reutilizável para o No Predictions
*(Pesquisa de prediction markets desportivos, todos os desportos e não só futebol. É um esqueleto para preencheres, não o artigo.)*

### Fórmulas de título
1. **"I built [No Predictions]. Here's how it will change the way you [trade/bet on] sports prediction markets."** (cópia direta da fórmula: autoria + benefício)
2. **"[N] sports prediction markets look 'obvious'. Here's what the data actually says."** (desmontar uma crença)
3. **"Before you buy YES on [jogo/evento], check these [N] things."** (checklist + ação)
4. **"Why most prediction-market sports traders lose, and the [ferramenta] I built to fix it."** (dor + solução)
5. **"From football to [ténis/NBA/F1]: one tool to research every sports market in [X] seconds."** (amplitude multi-desporto + velocidade)

### Esqueleto de secções
0. **Capa:** imagem com nome + "all sports prediction markets" + promessa numa linha.
1. **Hook (5–8 linhas curtas):** uma crença comum dos traders de mercados desportivos → "parece X" → "na realidade Y" → a consequência em dinheiro. *[Preencher: um erro típico, ex. confiar no preço do mercado ou no nome da equipa sem contexto]*
2. **A pergunta que importa:** "Nenhuma ferramenta responde a: ___?" → "É essa a pergunta que o No Predictions responde."
3. **A ideia / porque o construí:** o que te irritava + a tua regra de produto ("sem ruído", "uma resposta em segundos"). *[Preencher: história pessoal curta]*
4. **(Só se houver token) A história do token, contada cedo e honesta:** porque existe, o que **não** é, e quem controla as fees. *[Preencher; evitar promessas de preço]*
5. **O que o No Predictions faz mesmo:** uma metáfora ("como um analista que…") → os sinais que verifica (listas) → como os combina → o resultado final (score/veredicto) → linhas vermelhas. *[Preencher com as funcionalidades reais, para vários desportos]*
6. **Exemplo concreto:** 1–2 mercados reais (de preferência desportos diferentes) com screenshot: o que o mercado dizia, o que a ferramenta mostrou, o que aconteceu. *[Preencher com casos verificáveis, incluindo falhas]*
7. **Porque é diferente:** 4–6 pares "X, not Y" (ex. "Todos os desportos, não só futebol" · "Contexto, não só odds"). *[Preencher]*
8. **Prova de ritmo:** changelog com versões e datas. *[Preencher com releases reais]*
9. **Onde usar:** site / bot / alertas, e como partilhar com um grupo. *[Preencher]*
10. **(Opcional) Para holders/comunidade:** benefícios ligados ao produto, verificáveis. *[Preencher; evitar sorteios]*
11. **O que vem a seguir:** 4–6 itens do roadmap, idealmente com prazo. *[Preencher]*
12. **CTA:** uma ação ("Pesquisa antes de entrar no mercado") + links (site, X, Telegram/bot, GitHub se houver) + pedido de feedback + um agradecimento curto. *[Preencher]*

### Regras práticas (aprendidas com o artigo)
- Frases curtas, uma ideia por linha. Um título a cada ~150 palavras. Uma imagem a cada 1–2 secções.
- Mostrar números reais de uso (scans, utilizadores, mercados cobertos), que foi o que faltou ao CrawlScan.
- Usar exemplos de mercados de vários desportos para provar o "não só futebol".
- Se houver token, mantê-lo em 1–2 blocos e nunca terminar com "you are still early".
- Embeber os posts anteriores mais fortes para dar prova e contexto.
