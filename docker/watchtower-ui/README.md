# watchtower-ui

Interface web simples para a HTTP API de um contentor **Watchtower** já existente. É um contentor Ubuntu 26.04 com
**nginx + SSH**: o nginx serve uma página e faz de proxy para a API, juntando o token (que fica só no servidor e nunca
chega ao browser). O acesso SSH é feito com um utilizador configurável, só com chave pública e com sudo.

## Funcionalidades

| Ação | Interface | API do Watchtower |
|---|---|---|
| Atualizar todos os contentores | `POST /api/update` | `/v1/update` |
| Atualizar só algumas imagens | `POST /api/update?image=nginx,mariadb` | `/v1/update?image=...` |
| Ver métricas (atualização a cada 30 s) | `GET /api/metrics` | `/v1/metrics` |

Métricas mostradas: contentores verificados, atualizados e com falha na última verificação, total de
verificações e verificações ignoradas. Há também um registo das ações feitas na página.

## Arquitetura

```
browser ──► :8090 ─┐
ssh     ──► :2224 ─┤ watchtower-ui (nginx + sshd) ──► watchtower:8080 (HTTP API)
                   └────────────── rede watchtower-net ──────────────┘
```

| Contentor | Rede | Portas publicadas |
|---|---|---|
| `watchtower` (já existente) | `watchtower-net` | nenhuma (a API só é acessível dentro da rede) |
| `watchtower-ui` | `watchtower-net` | `8090` (web) e `2224` (SSH), configuráveis |

## Ficheiros

```
watchtower-ui/
├── Dockerfile                  # Ubuntu 26.04 + nginx + openssh-server + sudo
├── docker-compose.yml          # contentor ligado à rede externa do watchtower
├── entrypoint.sh               # host key SSH, utilizadores, config do nginx e arranque
├── ssh-utilizadores.sh         # utilizadores SSH, chaves e sudo em cada arranque
├── nginx.conf.template         # proxy para a API (token preenchido no arranque)
├── html/index.html             # página web (HTML + JS, sem dependências)
├── .env.example                # → .env
└── ssh/authorized_keys.example # → ssh/authorized_keys
```

Criados no 1.º arranque / pelo utilizador (no `.gitignore`): `.env`, `ssh/authorized_keys`, `hostkeys/`.

## Variáveis (`.env`)

| Variável | Omissão | Descrição |
|---|---|---|
| `SSH_USER` | `admin` | Utilizador SSH, com sudo sem password (definido no build) |
| `SSH_UID` | `1001` | UID do utilizador (definido no build; ex. igual ao do anfitrião, `id -u`) |
| `SSH_PUBKEY` | | Chave(s) pública(s) do utilizador, além de `ssh/authorized_keys`; várias separadas por `;` |
| `SSH_EXTRA_USER` | | Segundo utilizador SSH (opcional), criado em cada arranque |
| `SSH_EXTRA_PUBKEY` | | Chave(s) pública(s) do utilizador extra |
| `SSH_EXTRA_SUDO` | `1` | `1` = sudo sem password; `0` = sem sudo |
| `SSH_EXTRA_UID` | | UID do utilizador extra (opcional) |
| `UI_PORT` | `8090` | Porta da interface web no anfitrião |
| `SSH_PORT` | `2224` | Porta SSH no anfitrião |
| `CONTAINER_NAME` | `watchtower-ui` | Nome do contentor |
| `DATA_DIR` | `.` | Pasta de `ssh/` e `hostkeys/` (caminho absoluto numa stack do Portainer a partir do Git) |
| `WATCHTOWER_HOST` | `watchtower` | Nome do contentor watchtower na rede |
| `WATCHTOWER_PORT` | `8080` | Porta da API do watchtower |
| `WATCHTOWER_NETWORK` | `watchtower-net` | Rede Docker partilhada |
| `WATCHTOWER_HTTP_API_TOKEN` | — | Obrigatório; igual ao do watchtower |

Alterar `SSH_USER` ou `SSH_UID` obriga a reconstruir a imagem (`docker compose up -d --build`); as chaves e o
utilizador extra são aplicados em cada arranque.

## Pré-requisitos no contentor watchtower

O watchtower tem de ter a HTTP API ativa e estar na mesma rede.

**1. Variáveis de ambiente** (no compose ou `docker run` do watchtower; obriga a recriar o contentor):

| Variável | Valor | Notas |
|---|---|---|
| `WATCHTOWER_HTTP_API_UPDATE` | `true` | Ativa `/v1/update` |
| `WATCHTOWER_HTTP_API_METRICS` | `true` | Ativa `/v1/metrics` |
| `WATCHTOWER_HTTP_API_TOKEN` | token | O mesmo do `.env` da UI |
| `WATCHTOWER_HTTP_API_PERIODIC_POLLS` | `true` | Sem isto, ao ativar a API de update o watchtower deixa de verificar sozinho |

Não é preciso publicar a porta 8080 do watchtower.

**2. Rede partilhada:**

```bash
docker network create watchtower-net
docker network connect watchtower-net watchtower
```

Se o watchtower for gerido por compose, é melhor declarar a rede lá para não se perder ao recriar:

```yaml
services:
  watchtower:
    # ...
    networks:
      - watchtower-net

networks:
  watchtower-net:
    name: watchtower-net
    external: true
```

## Instalação

```bash
cd docker/watchtower-ui
cp .env.example .env
# editar .env: SSH_USER, SSH_UID, WATCHTOWER_HTTP_API_TOKEN (igual ao do watchtower)
cp ssh/authorized_keys.example ssh/authorized_keys
# colar a chave pública SSH em ssh/authorized_keys (ou usar SSH_PUBKEY no .env)
docker compose up -d --build
```

- Web: `http://<anfitrião>:8090`
- SSH: `ssh -p 2224 <SSH_USER>@<anfitrião>` (e depois `sudo -i`)

Em cada arranque o contentor:
- gera (só da 1.ª vez) a host key SSH em `hostkeys/`, para não mudar entre rebuilds;
- escreve o `~/.ssh/authorized_keys` a partir de `ssh/authorized_keys` e `SSH_PUBKEY` (linhas inválidas ignoradas);
- cria o utilizador extra, se definido, e aplica o sudo e o `AllowUsers`;
- gera a configuração do nginx a partir do template, valida-a (`nginx -t`) e arranca o nginx.

SSH só com chave pública: sem password e sem login de root.

## Verificação

```bash
# o watchtower está na rede?
docker network inspect watchtower-net --format '{{range .Containers}}{{.Name}} {{end}}'

# a UI chega à API?
curl -s http://localhost:8090/api/metrics | grep ^watchtower_

# SSH e sudo
ssh -p 2224 <SSH_USER>@<anfitrião> sudo -n true && echo ok

# logs
docker logs watchtower-ui
docker logs watchtower
```

| Sintoma | Causa provável |
|---|---|
| `502 Bad Gateway` | O watchtower não está na rede ou `WATCHTOWER_HOST` está errado |
| `401 Unauthorized` | Token diferente entre a UI e o watchtower |
| `404` em `/api/update` | Falta `WATCHTOWER_HTTP_API_UPDATE=true` no watchtower |
| SSH recusa a chave | Chave em falta ou inválida: ver `docker logs watchtower-ui` (linha `ssh: <utilizador> com N chave(s)`) |
| O update fica pendente muito tempo | Normal: o pedido só termina quando o pull e a recriação acabam (timeout de 15 min) |

## Segurança

- A página **não tem autenticação**. Não a publicar na internet sem login à frente (`auth_basic` no nginx ou o
  proxy reverso do NAS/router com autenticação).
- SSH só na rede local; não abrir a porta no router.
- O `.env`, as chaves e as host keys estão no `.gitignore`.
