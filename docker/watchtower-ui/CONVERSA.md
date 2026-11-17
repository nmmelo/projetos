# Conversa: watchtower-ui (interface web para o Watchtower)

Chat de 17 de novembro de 2026. As perguntas estão como foram escritas; as respostas estão resumidas, mas
mantêm as conclusões. Os ficheiros finais estão nesta pasta.

---

## 1. Interface web do Watchtower

**Pergunta:**
> existe alguma interface web para o container watchtower

**Resposta:**
Não. O Watchtower não tem interface web; a HTTP API serve só para disparar updates (`/v1/update`) e expor
métricas Prometheus (`/v1/metrics`).

Opções:

| Opção | Notas |
|---|---|
| Manter o Watchtower + Portainer | Visibilidade dos contentores pela UI do Portainer; métricas no Grafana |
| Tugtainer | UI com autenticação, cron, notificações, vários hosts por agentes |
| WUD (What's Up Docker) | Substituto mais direto, com limites de update (ex. só patch) |
| WatchWarden | Aceita as variáveis `WATCHTOWER_*`, políticas por contentor |
| Dockwatch | UI cuidada e notificações |
| Diun / Cup | Só notificação, sem updates automáticos |

---

## 2. Interface própria para a HTTP API

**Pergunta:**
> e fazer uma interface web para usar os HTTP API, sendo que esses dois container devem estar numa rede de docker

**Resposta:**
Contentor `nginx:alpine` que serve uma página HTML/JS e faz proxy para `watchtower:8080`, juntando o token
(o browser nunca o vê). Os dois contentores na rede `watchtower-net`; o watchtower sem portas publicadas.

- `POST /api/update` → `/v1/update` (todos ou `?image=a,b`)
- `GET /api/metrics` → `/v1/metrics` (lido a cada 30 s)
- Com a API de update ativa, o watchtower deixa de verificar sozinho: é preciso
  `WATCHTOWER_HTTP_API_PERIODIC_POLLS=true`.
- A página não tem autenticação: não expor à internet sem login à frente.

---

## 3. Só o contentor web

**Pergunta:**
> mas o container watchtower já existe, apenas preciso do container web

**Resposta:**
O compose passou a ter só o `watchtower-ui`, ligado à rede `watchtower-net` como rede externa. O nome do
watchtower, a porta, a rede e o token são configuráveis no `.env`. O nginx usa o DNS interno do Docker
(`127.0.0.11`) para resolver o watchtower em cada pedido, por isso a UI arranca mesmo que o watchtower esteja em
baixo. No watchtower existente basta ativar as variáveis da HTTP API e ligá-lo à rede
(`docker network connect watchtower-net watchtower`).

---

## 4. Sudo, utilizador e chave SSH

**Pergunta:**
> quero que tenha sudo, permita colocar um utilzidor, colocar o ssh_key, o ssh_id,

**Resposta:**
A imagem passou de `nginx:alpine` para Ubuntu 26.04 com nginx + openssh-server + sudo, no mesmo modelo do
`tesla-key`:

| Variável | Uso |
|---|---|
| `SSH_USER` / `SSH_UID` | Utilizador e UID (build); sudo sem password, sem password de login |
| `SSH_PUBKEY` + `ssh/authorized_keys` | Chave(s) pública(s), aplicadas em cada arranque |
| `SSH_EXTRA_*` | Segundo utilizador opcional, com ou sem sudo |
| `SSH_PORT` | Porta SSH no anfitrião (2224) |

A configuração do nginx é gerada no arranque a partir de `nginx.conf.template` (envsubst) e a host key SSH fica
persistente em `hostkeys/`.

---

## 5. Portal com login, base de dados, histórico e configuração

**Pergunta:**
> Podemos ter login, com fator humano, os dados ficam numa base de dados local, no dos mount, ter variáveis de ambiente
> como ssh user, local onde vai estar os mount. Deve ter também os logs, e para cada container que existir, vai guardado
> a informação das atualizações, podendo ver as versões que foram instaladas, quando. Na parte de configuração ter os
> logs, auditoria, utilizadores.

**Resposta:**
Foi apresentado o layout (login, contentores com histórico, configuração) e a dúvida sobre "fator humano": verificação
humana anti-bot, 2FA (TOTP) ou as duas.

**Pergunta:**
> Apenas a primeira, isto é o que WATCHTOWER_NOTIFICATION_URL

**Resposta:**
`WATCHTOWER_NOTIFICATION_URL` é o destino das notificações do Watchtower. Aponta para o portal
(`generic+http://watchtower-ui/api/webhook/<WEBHOOK_TOKEN>`) e, com `WATCHTOWER_NOTIFICATION_REPORT=true` e um template
JSON, o portal recebe no fim de cada verificação os contentores atualizados e os que falharam.

O nginx com a página estática passou a portal Python (Flask + Gunicorn atrás do nginx):

| Peça | Como |
|---|---|
| Login | Utilizador/password + desafio aritmético gerado localmente (SVG), bloqueio do IP após 5 falhas durante 15 min |
| Base de dados | SQLite em `DATA_DIR/db` |
| Histórico | Leitura do Docker (socket) a cada 60 s: mudança de imagem = atualização, com versão anterior/nova, origem e data; falhas pelo webhook |
| Configuração | Utilizadores (admin / só leitura), auditoria, logs (portal, watchtower, nginx) |
| Variáveis | `DATA_DIR`, `SSH_USER`, `SSH_UID`, `ADMIN_USER`, `ADMIN_PASSWORD`, `LOG_RETENTION_DAYS`, `WEBHOOK_TOKEN`, … |

O que acrescentar ao watchtower ficou num ficheiro de exemplo (mais tarde substituído por `watchtower/docker-compose.yml`).

---

## 6. Sem socket do Docker

**Pergunta:**
> Sim este ui vai estar apenas disponível na rede local com o docker, podemos é ter depois a saída web do portal noutra
> rede. Mas o portal preciso do socket, não pode recebe do outro container watchtower

**Resposta:**
Pode. O relatório do Watchtower passou a incluir todos os contentores verificados (`.Report.All`: nome, imagem, estado,
ID atual e novo, erro) e as linhas de log da sessão (`.Entries`). O portal constrói a lista, o histórico e os logs do
Watchtower só com isso; o socket ficou opcional (comentado no compose).

| | Sem socket (omissão) | Com socket |
|---|---|---|
| Lista e histórico | A cada verificação do Watchtower | Também a cada 60 s |
| Versão | Tag + ID da imagem | Label OCI ou tag + ID |
| Estado a correr/parado | Não | Sim |
| Logs do Watchtower | Os enviados nos relatórios | Em tempo real |

Saída web noutra rede: rede extra `web-net` (comentada) no compose e `PROXY_HOPS=2` atrás de um proxy reverso.

---

## 7. Porta na rede local

**Pergunta:**
> Mas eu quero aceder a parte web da minha rede local na porta x por exemplo 8098

**Resposta:**
Já está previsto: `UI_PORT=8098` no `.env` (mapeamento `${UI_PORT}:80`) e `http://<IP-do-anfitrião>:8098`. Opcional:
ligar só à placa da rede local com `"<IP>:${UI_PORT}:80"`. Não abrir a porta no router.

---

## 8. Compose para o Watchtower

**Pergunta:**
> podes gerar um docker-compose para o watchtower, não tenho o meu com docker-compose, deve ser com docker.

**Resposta:**
Criado `watchtower/docker-compose.yml` (+ `.env.example`): imagem `nickfedor/watchtower`, agendamento, cleanup, HTTP API,
relatório JSON para o portal e rede `watchtower-net`, sem portas publicadas. No README ficou a migração do `docker run`:
ver as opções atuais com `docker inspect`, tabela de equivalências (`--cleanup`, `--schedule`, `--interval`, …),
`docker rm -f watchtower` e `docker compose up -d`.

---

## 9. Imagem containrrr/watchtower

**Pergunta:**
> a imagem é containrrr/watchtower

**Resposta:**
O compose passou a usar `containrrr/watchtower` por omissão (`WATCHTOWER_IMAGE` no `.env`). Essa imagem já não é mantida e
fala com o Docker pela API 1.25, que o Docker 29 recusa; por isso ficou a variável `DOCKER_API_VERSION` (1.25 por omissão,
1.44 para Docker 29+). Se mesmo assim falhar, a alternativa é o fork `nickfedor/watchtower`, que aceita as mesmas variáveis.
