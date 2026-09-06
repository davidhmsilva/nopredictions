# "O favorito pré-jogo que não está a ganhar é ruído" — testado 2026-08-20

## Hipótese
A PM está bem calibrada pré-jogo. Logo, se o favorito a ~1.80 não está a ganhar
durante o jogo, isso é ruído e devíamos comprá-lo in-play.

## Veredicto: NÃO MEDIDO. O feed de preço está atrasado e fabrica o resultado.

## Universo
`market_observations`, 2026-05-28 → 2026-08-15. Mercados 1x2 filtrados pelo TEXTO
da pergunta — `market_group='1x2'` tem 6,5% de contaminação ("X to win the second
half?", "O/U 6.5", "to score first"). Lados resolvidos com
`fav_pressure_agent.resolve_side` (alias-aware); o matching exato descartava 40%
das linhas, o alias-aware descarta 3,3% e o que sobra são joins genuinamente
corrompidos ("Central Espanol FC" dentro de "Central African Republic vs
Liverpool Montevideo").

Funil: 230.211 linhas limpas → 963 fixtures → 654 sem linhas live (o braço live do
observer cobre pouco) → **130 fixtures utilizáveis / 1.220 observações in-play**.

Validação de orientação: favorito a ganhar aos 80'+ ganha 92,6% (ask 0,912);
a perder aos 80'+ ganha 6,2% (ask 0,106). Lado e marcador estão certos.

## O artefacto
Invertendo o 1x2 pré-jogo de-vigado para (λ_casa, λ_fora) e calculando o justo
Poisson condicional a (minuto, marcador), define-se

    λ_atraso = (ask − preço_pré_jogo) / (justo_do_estado − preço_pré_jogo)
    0 = o ask ainda está no preço pré-jogo · 1 = o ask está no justo do estado

| estado | n | pré | justo | ask | realizado | λ mediana |
|---|---|---|---|---|---|---|
| favorito atrás | 140 | 0,540 | 0,123 | 0,283 | 0,257 | +0,73 |
| favorito empatado | 447 | 0,598 | 0,457 | 0,545 | 0,468 | +0,48 |
| favorito a ganhar | 633 | 0,736 | 0,945 | 0,885 | 0,927 | +0,90 |
| **atrás 1-30'** | **24** | **0,550** | **0,247** | **0,496** | **0,250** | **+0,06** |

Por tempo desde a última mudança de marcador (histórico do MARCADOR, nunca do
preço — logo não seleciona nada correlacionado com o resultado):

| marcador estável há | n | λ p25 | mediana | p75 |
|---|---|---|---|---|
| 0' (1ª vez que se vê este marcador) | 262 | **+0,05** | +0,67 | +0,94 |
| 1-19' | 228 | +0,68 | +0,85 | +0,99 |
| 20-39' | 234 | +0,55 | +0,77 | +0,94 |
| 40'+ | 112 | +0,40 | +0,63 | +0,81 |

**No primeiro poll depois de um golo, um quarto dos preços registados não se
mexeu de todo.** O observer lê `bestBid`/`bestAsk` do Gamma, que atrasa face ao
CLOB, e poleia a cada ~30 min.

## O que o atraso fabricava

| | sem controlo | com marcador estável ≥20' |
|---|---|---|
| favorito **atrás** | −57,6% CI[−81,6, −29,1] · ask 0,486 | **+16,8%** CI[−68,6, +117,3] · ask 0,239 |
| favorito **empatado** | −11,0% CI[−29,0, +7,1] | −22,8% CI[−46,1, +1,8] |
| favorito **a ganhar** | +29,4% CI[+11,2, +48,2] | **−2,9%** CI[−30,9, +20,8] |

(1 entrada por fixture+estado, ao ask, líquido de fee de taker, `ask<=0.85` —
o guard documentado em [[finding-observation-calibration-broken]].)

O ask de "favorito atrás" cai de 0,486 para 0,239 quando se exige um preço
fresco. Os −57,6% eram compras a um preço que **não existia**. Simetricamente,
os +29,4% em "a ganhar" eram compras a um ask preso abaixo do justo. **Os dois
números eram o mesmo bug com sinais opostos.**

Com preço fresco nada se distingue de zero, e n = 18/51/17 fixtures — muito
abaixo da regra das 200 seleções.

## Consequências
1. **Nenhum backtest in-play pode sair de `market_observations` sem controlo de
   frescura.** Mesmo classe de bug dos phantom exits do convergence
   (+141% paper vs +3,4% real nas MESMAS 15 decisões).
2. ⚠️ O "in-play drift −0,65pp/15min" de [[finding-observer-model-vs-pm]] tem de
   ser re-verificado: é exatamente a forma de um feed atrasado a apanhar-se.
3. O instrumento certo é o CLOB a 60s (`pm_ticks`) — mas só tem **um dia**
   (07-21→07-22, 371k linhas) e `live_fixture_ticks` tem 46 linhas.

## Para responder à pergunta a sério
Voltar a ligar `tick_daemon.sh` (CLOB, 60s) + o braço de score. ~3 meses dão
n≈200 fixtures com o favorito atrás. Sem isso, a hipótese fica por medir — o que
NÃO é o mesmo que confirmada.
