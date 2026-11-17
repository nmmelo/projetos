# watchtower-ui

Portal web para um **Watchtower** já existente. Corre num contentor Ubuntu 26.04 com **nginx + Python (Flask/Gunicorn) +
SSH** e guarda tudo numa base de dados **SQLite** na pasta de dados montada (`DATA_DIR`).

| Área | O que tem |
|---|---|
| Login | Utilizador e password com verificação humana (desafio gerado localmente), bloqueio do IP após tentativas falhadas, sessão com expiração |
| Painel | Estado da ligação ao Watchtower, métricas da última verificação, resumo dos contentores, últimas atualizações, atualizar todos ou só algumas imagens |
| Contentores | Lista com a versão atual, a imagem, a data em que começou a ser seguido e a última atualização; botão para atualizar cada um |
| Histórico | Por contentor: data, versão anterior → versão nova (com ID da imagem), origem (Watchtower, Manual (utilizador), Registo inicial) e resultado (OK/Falhou com o erro) |
| Configuração › Utilizadores | Criar, mudar perfil (administrador / só leitura), ativar/desativar, repor password, apagar |
| Configuração › Auditoria | Logins, logins falhados e bloqueados, updates pedidos e o resultado, alterações a utilizadores — com utilizador, IP e data |
| Configuração › Logs | Portal, Watchtower (linhas de cada relatório, ou em tempo real com o socket), nginx (acessos e erros), com filtro |
| SSH | Utilizador configurável, só chave pública, com sudo |

## Arquitetura

```
browser ──► :8090 ─┐                  ┌─ nginx ─► Gunicorn (portal Flask) ─► SQLite (DATA_DIR/db), logs (DATA_DIR/logs)
ssh     ──► :2224 ─┤  watchtower-ui   │                         │
                   │                  └─ sshd                   └─► watchtower:8080 (HTTP API: update, métricas)
watchtower ── relatório JSON no fim de cada verificação ──► watchtower-ui:80/api/webhook/<token>
         └──────────────────────── rede watchtower-net ─────────────────────────┘
```

**Sem socket do Docker (omissão).** Tudo vem do Watchtower:

| Dado | Origem |
|---|---|
| Lista de contentores | Relatório de cada verificação (todos os contentores verificados) |
| Histórico de versões | Relatório: estado (Atualizado, Falhou, Sem alterações, Ignorado, Desatualizado), imagem atual → nova, erro |
| Logs do Watchtower | Linhas de log enviadas com cada relatório (guardadas na base de dados) |
| Métricas e botões de update | HTTP API do Watchtower |

O histórico é construído assim:
- O primeiro relatório em que um contentor aparece cria o **Registo inicial** (versão e ID da imagem que estavam instalados).
- Estado **Atualizado**: regista versão/ID anterior → nova, com origem **Watchtower** ou **Manual (utilizador)** se o update
  foi pedido no portal.
- Estado **Falhou**: regista a falha com o erro.
- Se o ID da imagem mudou entre dois relatórios sem o Watchtower o ter atualizado (ex. `docker compose pull` à mão),
  regista com origem **Fora do Watchtower**.
- A versão é a **tag** da imagem (ex. `11.4`, `alpine`); o ID curto da imagem fica sempre ao lado (essencial nas imagens
  `latest`).

Limitações sem socket: só aparecem os contentores que o Watchtower vigia; a informação é atualizada a cada verificação
(não em tempo real); não há estado a correr/parado nem a label `org.opencontainers.image.version`.

**Com socket (opcional).** Descomentando o volume `/var/run/docker.sock` no compose, o portal também lê o Docker a cada
`SYNC_INTERVAL` segundos: estado a correr/parado, versão pela label OCI, logs do watchtower em tempo real e deteção imediata
de mudanças. Os dois modos juntam-se sem duplicar registos.

**Saída web noutra rede.** O portal fica na rede `watchtower-net` (para falar com o watchtower); para o publicar através de
outra rede (ex. a de um proxy reverso), descomentar `web-net` no compose (`WEB_NETWORK` no `.env`) e pôr `PROXY_HOPS=2`.

## Ficheiros

```
watchtower-ui/
├── Dockerfile                  # Ubuntu 26.04 + nginx + python3-flask + gunicorn + openssh + sudo
├── docker-compose.yml          # contentor na rede externa do watchtower
├── entrypoint.sh               # pastas de dados, host key, utilizadores SSH, socket do Docker, portal e nginx
├── ssh-utilizadores.sh         # utilizadores SSH, chaves e sudo em cada arranque
├── nginx.conf                  # proxy para o portal + ficheiros estáticos
├── portal/
│   ├── app.py                  # rotas: login, painel, contentores, configuração, webhook
│   ├── core.py                 # SQLite, relatórios (webhook), API do Watchtower, Docker opcional, limpeza
│   ├── templates/              # páginas (Jinja)
│   └── static/style.css
├── watchtower-exemplo.yml      # o que acrescentar ao compose do watchtower
├── .env.example                # → .env
└── ssh/authorized_keys.example # → ssh/authorized_keys
```

Na pasta `DATA_DIR` (no `.gitignore`):

| Pasta | Conteúdo |
|---|---|
| `db/` | `watchtower-ui.db` (utilizadores, auditoria, contentores, histórico, relatórios) e `.secret_key` (sessões) |
| `logs/` | `portal.log` (rotação 3×5 MB), `nginx-access.log`, `nginx-error.log` (rotação a 10 MB) |
| `ssh/authorized_keys` | Chaves públicas do utilizador SSH |
| `hostkeys/` | Host key SSH (criada no 1.º arranque) |

## Variáveis (`.env`)

| Variável | Omissão | Descrição |
|---|---|---|
| `DATA_DIR` | `.` | Pasta dos dados no anfitrião (mounts). Caminho absoluto numa stack do Portainer a partir do Git |
| `SSH_USER` | `admin` | Utilizador SSH com sudo; também é quem corre o portal (build) |
| `SSH_UID` | `1001` | UID do utilizador; de preferência igual ao do anfitrião (`id -u`) (build) |
| `SSH_PUBKEY` | | Chave(s) pública(s), além de `ssh/authorized_keys`; várias separadas por `;` |
| `SSH_EXTRA_USER` / `_PUBKEY` / `_SUDO` / `_UID` | | Segundo utilizador SSH (opcional) |
| `ADMIN_USER` | `admin` | Primeiro administrador do portal (só no 1.º arranque) |
| `ADMIN_PASSWORD` | | Password inicial; vazio = gerada e mostrada em `docker logs watchtower-ui` |
| `LOG_RETENTION_DAYS` | `90` | Dias de auditoria e relatórios guardados (o histórico de versões não é apagado) |
| `SYNC_INTERVAL` | `60` | Segundos entre leituras do Docker (só com socket) |
| `SESSION_HOURS` | `8` | Duração da sessão |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Bloqueio do IP após tentativas falhadas |
| `PROXY_HOPS` | `1` | `2` se houver outro proxy reverso à frente (para registar o IP real) |
| `WEB_NETWORK` | `proxy-net` | Rede extra para a saída web (só se `web-net` for descomentada no compose) |
| `WEBHOOK_TOKEN` | | Token do endpoint dos relatórios do Watchtower |
| `UI_PORT` / `SSH_PORT` | `8090` / `2224` | Portas no anfitrião |
| `WATCHTOWER_HOST` / `WATCHTOWER_PORT` | `watchtower` / `8080` | Endereço da HTTP API na rede |
| `WATCHTOWER_CONTAINER` | `watchtower` | Nome do contentor para ler os logs (só com socket) |
| `WATCHTOWER_NETWORK` | `watchtower-net` | Rede Docker partilhada |
| `WATCHTOWER_HTTP_API_TOKEN` | — | Obrigatório; igual ao do watchtower |

## No contentor watchtower (já existente)

Ver `watchtower-exemplo.yml`. Resumo:

| Variável | Valor | Para quê |
|---|---|---|
| `WATCHTOWER_HTTP_API_UPDATE` | `true` | Botões de atualizar |
| `WATCHTOWER_HTTP_API_METRICS` | `true` | Métricas no painel |
| `WATCHTOWER_HTTP_API_TOKEN` | token | Igual ao do portal |
| `WATCHTOWER_HTTP_API_PERIODIC_POLLS` | `true` | Mantém as verificações agendadas com a API ativa |
| `WATCHTOWER_NOTIFICATION_URL` | `generic+http://watchtower-ui/api/webhook/<WEBHOOK_TOKEN>` | Para onde o Watchtower envia o relatório de cada verificação |
| `WATCHTOWER_NOTIFICATION_REPORT` | `true` | Envia o relatório completo (todos os contentores verificados) |
| `WATCHTOWER_NOTIFICATIONS_LEVEL` | `info` | Nível das linhas de log incluídas no relatório |
| `WATCHTOWER_NOTIFICATION_TEMPLATE` | ver exemplo | Põe o relatório em JSON para o portal |

E ligá-lo à rede `watchtower-net`. Sem socket, o relatório é **obrigatório**: é dele que vêm a lista de contentores, o
histórico e os logs do Watchtower. A lista aparece depois da primeira verificação (ou de um "Atualizar todos" no portal).

## Tokens

```bash
openssl rand -hex 32    # WATCHTOWER_HTTP_API_TOKEN (igual nos dois contentores)
openssl rand -hex 32    # WEBHOOK_TOKEN (no .env do portal e no URL de notificação do watchtower)
```

## Instalação

```bash
docker network create watchtower-net                 # se ainda não existir
cd docker/watchtower-ui
cp .env.example .env                                 # DATA_DIR, SSH_USER, SSH_UID, ADMIN_*, tokens
cp ssh/authorized_keys.example ssh/authorized_keys   # (em DATA_DIR) colar a chave pública SSH
docker compose up -d --build
docker logs watchtower-ui                            # password inicial, se ADMIN_PASSWORD estiver vazio
```

Depois, no watchtower: acrescentar as variáveis e a rede (`watchtower-exemplo.yml`) e recriá-lo.

- Portal: `http://<anfitrião>:8090` → mudar a password em **Mudar password**
- SSH: `ssh -p 2224 <SSH_USER>@<anfitrião>`

## Verificação

```bash
docker network inspect watchtower-net --format '{{range .Containers}}{{.Name}} {{end}}'
docker exec watchtower-ui curl -s http://127.0.0.1/saude
docker exec -u <SSH_USER> watchtower-ui sqlite3 /data/db/watchtower-ui.db \
  "select ts,container,from_version,to_version,source,result from updates order by id desc limit 10;"
```

| Sintoma | Causa provável |
|---|---|
| "Sem ligação à API do Watchtower" | O watchtower não está na rede, `WATCHTOWER_HOST` errado ou falta `WATCHTOWER_HTTP_API_METRICS=true` |
| "HTTP 401" no painel | Token diferente entre o portal e o watchtower |
| Lista de contentores vazia | Ainda não houve verificação, ou o relatório não chega: ver `docker logs watchtower` (erros de notificação) e Configuração › Logs › Portal (`webhook recusado` = token errado). Alternativa ao URL: `generic://watchtower-ui/api/webhook/<token>?disabletls=yes` |
| Relatório chega mas sem contentores | Template não aplicado: ver Configuração › Logs › Portal (`mensagem não estruturada`) e o `WATCHTOWER_NOTIFICATION_TEMPLATE` |
| Com socket: lista vazia | Permissões do socket: ver `docker logs watchtower-ui` (linha `docker: socket com GID ...`) |
| SSH recusa a chave | Ver `docker logs watchtower-ui` (linha `ssh: <utilizador> com N chave(s)`) |

## Segurança

- Portal e SSH só na rede local; para acesso externo, pôr um proxy reverso com HTTPS à frente (e `PROXY_HOPS=2`).
- Por omissão o portal **não** tem acesso ao socket do Docker. Se o montar: o `:ro` não torna a API só de leitura, e com o
  sudo do utilizador SSH o acesso SSH passa a equivaler a acesso ao anfitrião.
- Passwords em hash (Werkzeug), proteção CSRF nos formulários, cookies `HttpOnly`/`SameSite=Lax`.
- O `.env`, a base de dados, os logs, as chaves e as host keys estão no `.gitignore`.
