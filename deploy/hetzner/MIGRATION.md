# Migração Mac → Hetzner

Objetivo: tudo o que hoje corre no Mac (2 daemons no launchd + 13 linhas de cron)
passa a correr num VPS sempre ligado. O site continua no Vercel e a base de dados
no Supabase; só muda a máquina que corre os agentes e os gravadores.

**Regra única desta migração: nunca as duas máquinas ao mesmo tempo.** Dois
processos escrevem as mesmas observações em duplicado (já aconteceu, 22h de linhas
×2.5 em 2026-08-14) e gastam a mesma chave da api-football (já a esgotou). A ordem
dos passos abaixo existe por causa disto.

## O que fica a correr

| unidade | o que é | no Mac era |
|---|---|---|
| `np-daemon@pressure-daemon` | `pressure_daemon.sh` → s16/s17/s18 | launchd `com.nopredictions.pressure` |
| `np-daemon@settled-sweep` | `settled_sweep_observer.py --interval 30` | launchd `com.nopredictions.settled_sweep` |
| `np-pressure-settle.timer` | `pressure_agent.py --settle`, :05 e :35 | cron_guard --gap 25 |
| `np-pressure-health.timer` | `pressure_health.py --recover`, de 10 em 10 min | */10 |
| `np-sweep-settle.timer` | `settled_sweep_observer.py --settle`, :15 e :45 | cron_guard --gap 25 |
| `np-stage-a.timer` | Football-Data, 05:00 UTC | --daily 07:00 (hora do Mac, UTC+2) |
| `np-nfl-agent.timer` | `nfl_agent.py --once`, 5 em 5 min | */5 |
| `np-nfl-live.timer` | `nfl_live_recorder.py --once`, cada minuto | * |
| `np-factory-run.timer` | `factory_cli.py run`, cada minuto | * |
| `np-factory-settle.timer` | `factory_cli.py settle`, 10 em 10 min | */10 |
| `np-factory-grid.timer` | `factory_cli.py grid --refresh --register`, 04:00 UTC | --daily 06:00 (UTC+2) |
| `np-soccer-live.timer` | `soccer_live_recorder.py --once`, cada minuto | * |
| `np-soccer-live-settle.timer` | `soccer_live_recorder.py --settle`, :10 e :40 | */30 |
| `np-lab-runner.timer` | `lab_strategy_runner.py --once`, 5 em 5 min | */5 |
| `np-lab-settle.timer` | `lab_strategy_runner.py --settle`, :20 e :50 | cron_guard --gap 25 |

Todos os comandos, flags, `AF_COUNTER_NAME` e ficheiros de log vivem num só sítio:
[`np-job.sh`](np-job.sh). Os timers só dizem *quando*.

O que desaparece, e porquê:
- **`cron_guard.sh`**: existia porque o cron do macOS salta os jobs perdidos
  durante o sono. Um timer com `Persistent=true` recupera sozinho, e um serviço
  `oneshot` nunca arranca duas vezes em paralelo, portanto um job de minuto a
  minuto que demore 70s atrasa o seguinte em vez de o duplicar.
- **O `sleep`/`caffeinate`**: o servidor não dorme.
- **Os locks e heartbeats do `pressure_daemon.sh` ficam**: o `pressure_health.py`
  lê o heartbeat e, sem ele, dava sempre WEDGED. Custam nada e continuam a
  apanhar um wrapper encravado.

⚠️ **Não migrados, de propósito:** `late_goals_daemon.sh` (estava `#PAUSED`),
`flb_daemon.sh` (fechado como morto), `inplay_daemon.sh`, `convergence_daemon.sh`
e `tick_daemon.sh`. Confirma no Mac com `launchctl list | grep nopredictions` e
`crontab -l` que não corre mais nada. Se aparecer alguma coisa, é um novo `case`
em `np-job.sh` mais um timer ou `np-daemon@<nome>`.

## 1. Comprar o servidor

- **Plano:** 4 vCPU / 8 GB de RAM (gama CX ou CPX), 80 GB de disco.
  O trabalho é quase todo I/O de rede; os 8 GB são para o `factory grid` diário.
- **Imagem:** Ubuntu 24.04.
- **Localização:** Falkenstein / Nuremberg / Helsinki. Só lemos dados e estamos
  em paper.
- **IPv4: mantém.** O GitHub só fala IPv4, e o `git clone` falha sem ele.
- **Chave SSH:** adiciona a tua na criação. Não uses password.

## 2. Preparar o que vem do Mac (no Mac)

```bash
cd ~/agente
# as versões exatas que correm hoje — o install.sh usa este ficheiro
ingest/.venv/bin/pip freeze > deploy/hetzner/requirements-mac.txt
git add deploy/hetzner/requirements-mac.txt && git commit -m "Pin the Mac venv for the server" && git push
```

⚠️ **O `ingest/.env` tem `POLYMARKET_PRIVATE_KEY`.** Tudo o que corre é paper;
nenhum destes jobs precisa da chave da carteira. Copia um `.env` **sem essa linha**
para o servidor. Uma chave privada numa máquina exposta à internet é o pior
segredo que se pode perder.

```bash
grep -v '^POLYMARKET_PRIVATE_KEY' ingest/.env > /tmp/server.env
scp /tmp/server.env root@<IP>:/root/nopredictions.env && rm /tmp/server.env
```

## 3. Instalar (no servidor)

```bash
ssh root@<IP>
# Para correr já um branch em vez do main, passa-o como argumento ao install.sh.
git clone https://github.com/davidhmsilva/nopredictions.git /tmp/np
bash /tmp/np/deploy/hetzner/install.sh main
mv /root/nopredictions.env /opt/nopredictions/ingest/.env
chown np:np /opt/nopredictions/ingest/.env && chmod 600 /opt/nopredictions/ingest/.env
```

O `install.sh`:
- põe o relógio em UTC;
- cria o utilizador `np` e clona o repositório para `/opt/nopredictions`;
- cria o venv com Python 3.9, o mesmo do cron do Mac;
- instala as unidades systemd e configura a rotação de logs;
- fecha a firewall a tudo menos SSH.

**Não arranca nada.**

Endurecer o SSH (uma vez):

```bash
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

## 4. Testes (no servidor)

```bash
cd /opt/nopredictions/agent
sudo -u np /opt/nopredictions/deploy/hetzner/np-job.sh py -m pytest tests -q
```

Tem de dar o mesmo que no Mac. Uma falha aqui é quase sempre uma dependência que
faltou na lista.

## 5. Dry runs: a rede de um datacenter não é a de casa

Alguns serviços tratam IPs de datacenter de forma diferente. A ESPN já rejeita
User-Agents de browser. Todos estes comandos correm sem escrever na base de
dados:

```bash
J="sudo -u np /opt/nopredictions/deploy/hetzner/np-job.sh py"
$J venues.py --probe                          # Kalshi (139 séries)
$J ht_pressure_agent.py --once --dry-run      # api-football + ESPN + CLOB
$J settled_sweep_observer.py --once --dry-run # Gamma + CLOB
$J nfl_agent.py --once --dry-run              # Odds API, sem gastar créditos
$J lab_strategy_runner.py --spec '{"market":"ou25","side":"over","leagues":null}' --window 1440
```

O que procurar:
- **403 ou 429 onde no Mac dava 200.** A ESPN é a mais sensível; o scoreboard
  tem de responder.
- **`DATABASE_URL` a ligar.** Se falhar com um erro de IPv6, usa o host do
  *pooler* do Supabase, que é IPv4.

⚠️ Os gravadores (`soccer_live_recorder`, `nfl_live_recorder`) não têm
`--dry-run`: escrevem ficheiros locais, não a base de dados. São testados no
passo 7.

## 6. A troca (a única parte com ordem obrigatória)

**No Mac, desliga tudo primeiro:**

```bash
crontab -l > ~/agente/reports/crontab_backup_$(date +%F)_pre_hetzner.txt
crontab -r
launchctl bootout gui/$(id -u)/com.nopredictions.pressure
launchctl bootout gui/$(id -u)/com.nopredictions.settled_sweep
# (se estiverem em /Library/LaunchDaemons: sudo launchctl bootout system/com.nopredictions.<nome>)
sleep 5; pgrep -fl "pressure_agent|settled_sweep|recorder|factory_cli|nfl_agent" || echo "Mac limpo"
```

Só avança com **"Mac limpo"**.

**Copia o estado que não está no git:**

```bash
cd ~/agente/agent
rsync -av .af_calls_*.json .nfl_odds_cache.json root@<IP>:/opt/nopredictions/agent/
rsync -av data/ root@<IP>:/opt/nopredictions/agent/data/
ssh root@<IP> chown -R np:np /opt/nopredictions/agent
```

- `.af_calls_*.json` são os contadores de hoje da api-football. Sem eles, o
  servidor começa o dia a pensar que não gastou nada.
- `.nfl_odds_cache.json` é o último snapshot da Odds API, cujos créditos são
  racionados.
- `data/` são os tapes `soccer_live/` e `nfl_live/`: históricos, gitignored e
  impossíveis de regravar.

**No servidor, liga:**

```bash
systemctl enable --now np-daemon@pressure-daemon np-daemon@settled-sweep
systemctl enable --now $(cd /etc/systemd/system && ls np-*.timer)
```

## 7. Verificar (primeira hora)

```bash
systemctl list-timers 'np-*'                 # 13 timers com próxima execução
systemctl status 'np-daemon@*'               # os 2 daemons active (running)
systemctl --failed                           # vazio
tail -f /opt/nopredictions/agent/pressure_agent.log
tail -3 /opt/nopredictions/agent/pressure_health.log   # deve chegar a OK
ls -la /opt/nopredictions/agent/data/soccer_live/      # o ficheiro de hoje a crescer
cat /opt/nopredictions/agent/.af_calls_*.json          # gasto da chave
journalctl -u 'np-job@*' --since -1h -p warning        # jobs que falharam
```

Na base de dados, a prova final é a mesma que o `pressure_health.py` usa:
`max(observed_at)` em `pressure_observations` a avançar minuto a minuto.

⚠️ **Nas primeiras leituras, o `pressure_health` pode dar `WEDGED`.** Até o
wrapper escrever o primeiro heartbeat, o ficheiro não existe. Tem de passar a
`OK` em menos de 10 minutos. Se não passar, `systemctl restart
np-daemon@pressure-daemon`.

## 8. Operação do dia a dia

```bash
# atualizar o código (o Vercel continua a fazer o deploy do site sozinho)
sudo -u np git -C /opt/nopredictions pull --ff-only
systemctl restart 'np-daemon@*'    # daemons: o CLAUDE.md avisa sempre "restart required"
# os timers apanham o código novo na execução seguinte, sem restart

# parar um job
systemctl disable --now np-factory-run.timer

# correr um job já, fora do horário
systemctl start np-job@pressure-settle
```

Se uma mudança acrescentar um job novo: um `case` em `np-job.sh`, um
`np-<nome>.timer` em `systemd/`, `cp` para `/etc/systemd/system/`, `daemon-reload`,
`enable --now`.

## Voltar atrás

No servidor: `systemctl disable --now 'np-*.timer' 'np-daemon@*'`.

No Mac: `crontab ~/agente/reports/crontab_backup_<data>_pre_hetzner.txt` e
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nopredictions.*.plist`.

Copia de volta `agent/.af_calls_*.json` e `agent/data/`, pela mesma regra: uma
máquina de cada vez.

## Notas

- **Os logs passam a estar em UTC.** No Mac estavam duas horas à frente, e o
  CLAUDE.md avisa disso em vários sítios. No servidor já não é preciso fazer
  essa conta.
- **O uptime deixa de ser uma variável.** Com o Mac, o `uptime 78%` e os buracos
  de 6h eram a restrição nº 2 da cobertura. A partir da troca, um buraco no tape
  é um bug, não o Mac a dormir.
- **Não corras um LLM nesta máquina.** Os gravadores de minuto a minuto precisam
  do CPU. O brief do Game Center e o Lab continuam no Vercel, via API.
