# tesla-key

Contentor Ubuntu 26.04 com **nginx + SSH** para alojar, num Synology, a chave pública da Tesla Fleet API em
`https://url/.well-known/appspecific/com.tesla.3p.public-key.pem`.

O nginx só responde a esse caminho; tudo o resto devolve 404. O acesso SSH (utilizador `SSH_USER`, com sudo) serve para
gerar e publicar a chave. O comando `tesla-setup` (template incluído) faz os passos da Tesla dentro do contentor e o **gateway** (porta 8000,
só na rede Docker) serve os dados do carro a outros contentores, como o portal.

## Estrutura

```
tesla-key/
├── .env.example            # template do contentor (utilizador, UID, portas)
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── nginx.conf
├── API.md                  # referência da API para clientes
├── openapi.yaml            # especificação OpenAPI
├── tesla-setup.sh          # instalado como /usr/local/bin/tesla-setup
├── gateway/
│   ├── common.py           # base de dados, clientes, tokens Tesla, logs
│   ├── gateway.py          # API de clientes + Status API (porta 8000, rede Docker)
│   ├── admin.py            # portal admin web (porta 8001, LAN)
│   ├── cli.py              # gestão por linha de comandos (via tesla-setup)
│   └── cliente_exemplo.py  # exemplo de cliente para o portal
├── ssh/authorized_keys     # chave pública SSH de quem entra no contentor
├── html/.well-known/appspecific/com.tesla.3p.public-key.pem
├── tesla/                  # montado em /opt/tesla
│   ├── tesla.env.example   # template da Tesla (domínio, client id/secret, região)
│   ├── tesla.env           # cópia preenchida (não vai para o Git)
│   ├── keys/               # private-key.pem / public-key.pem (não vai para o Git)
│   ├── tokens.json         # access/refresh token (não vai para o Git)
│   └── gateway.db          # SQLite: clientes, definições, login, logs (não vai para o Git)
└── hostkeys/               # criado no 1.º arranque (host key SSH persistente)
```

## Templates

| Ficheiro | Copiar para | Variáveis |
|---|---|---|
| `.env.example` | `.env` | `SSH_USER`, `SSH_UID`, `HTTP_PORT`, `SSH_PORT`, `CONTAINER_NAME`, `ADMIN_PORT`, `DOCKER_NETWORK` |
| `tesla/tesla.env.example` | `tesla/tesla.env` | `TESLA_DOMAIN`, `TESLA_CLIENT_ID`, `TESLA_CLIENT_SECRET`, `TESLA_REGION`, `TESLA_REDIRECT_URI`, `TESLA_SCOPES` |

## Portas

| Porta NAS (`.env`) | Contentor | Uso |
|---|---|---|
| `HTTP_PORT` (8088) | 80 | nginx → destino do Proxy Reverso do DSM |
| `SSH_PORT` (2223) | 22 | SSH (só LAN, não abrir no router) |
| `ADMIN_PORT` (8089) | 8001 | Portal admin (só LAN, não abrir no router) |
| — (sem publish) | 8000 | Gateway API + Status API, só na rede Docker (`http://tesla-key:8000`) |

## Comando `tesla-setup`

| Comando | O que faz |
|---|---|
| `tesla-setup chaves` | Gera o par EC prime256v1 em `/opt/tesla/keys` (se não existir) e publica a pública |
| `tesla-setup verificar` | Lê a chave pelo domínio público (HTTPS) e compara com a local |
| `tesla-setup partner` | Token de partner, registo do domínio e confirmação da chave registada |
| `tesla-setup url` | Mostra o URL de autorização OAuth |
| `tesla-setup token <code>` | Troca o `code` (ou o URL de redirect inteiro) por tokens → `tokens.json` |
| `tesla-setup emparelhar` | Mostra o link `https://tesla.com/_ak/<domínio>` |
| `tesla-setup veiculos` | `GET /api/1/vehicles` |
| `tesla-setup refresh` | Renova o access token |
| `tesla-setup estado` | Resumo da configuração |
| `tesla-setup admin` | Define o utilizador/password do portal admin |
| `tesla-setup cliente add NOME [DESCRIÇÃO]` | Cria um cliente e mostra a chave (só uma vez) |
| `tesla-setup cliente list` | Lista os clientes (prefixo, estado, último uso, pedidos) |
| `tesla-setup cliente on\|off\|rm\|chave NOME` | Ativa, desativa, apaga ou gera nova chave |
| `tesla-setup status-api on\|off\|token\|info` | Gere a Status API e o token Bearer |
| `tesla-setup definicao [CHAVE [VALOR]]` | `cache_segundos`, `retencao_logs_dias` |
| `tesla-setup logs [N] [NIVEL]` | Últimos N registos (INFO, WARNING, ERROR) |
| `tesla-setup logs-limpar` | Apaga logs mais antigos que a retenção |
| `tesla-setup resumo` | Estado do gateway em JSON |

## Gateway

```
                      ┌──────────── tesla-key ────────────┐
Portal / outros  ──►  │ :8000 /api/...     (X-API-Key)     │ ──► Tesla Fleet API
(rede Docker)         │ :8000 /status/v1/  (Bearer token)  │
Browser (LAN)    ──►  │ :8001 portal admin (login)         │
                      └── gateway.db · tokens.json ────────┘
```

### Clientes (`/api`)

Referência completa para quem desenvolve o cliente: [`API.md`](API.md) e [`openapi.yaml`](openapi.yaml).

Cada projeto tem o seu cliente e a sua chave (`X-API-Key`). As chaves são guardadas em hash e só são
mostradas na criação. Um cliente desativado deixa de ter acesso de imediato.

| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/health` | Sem autenticação |
| GET | `/api/token/status` | Validade do access token |
| GET | `/api/vehicles` | Lista de veículos |
| GET | `/api/vehicles/<vin>` | Estado (`online`/`asleep`), não acorda o carro |
| GET | `/api/vehicles/<vin>/data` | `vehicle_data` com cache (`?endpoints=charge_state;drive_state`, `?fresh=1`) |
| POST | `/api/vehicles/<vin>/wake` | Acorda o carro (mais caro, gasta bateria) |
| GET | `/api/fleet/<caminho>` | Passthrough só de leitura para `/api/1/<caminho>` |

Com o carro a dormir, `/data` devolve **408**.

```bash
# no portal (mesma rede Docker)
TESLA_GATEWAY_URL=http://tesla-key:8000
TESLA_GATEWAY_API_KEY=tk_...        # chave do cliente "portal"
curl -H "X-API-Key: $TESLA_GATEWAY_API_KEY" http://tesla-key:8000/api/vehicles
```
Exemplo em Python: `gateway/cliente_exemplo.py`.

### Status API (`/status/v1`)

Para outro portal ver o estado do gateway (sem acesso aos dados do carro). Desativada por omissão; quando
desativada responde 404. Autenticação com `Authorization: Bearer <token>` (token gerado no portal admin ou com
`tesla-setup status-api token`; gerar um novo invalida o anterior).

| Endpoint | Conteúdo |
|---|---|
| `/status/v1/health` | `{"ok": true}` |
| `/status/v1/resumo` | Configuração, token Tesla, clientes, contagens das últimas 24 h |
| `/status/v1/clientes` | Nome, estado, último uso e pedidos de cada cliente |
| `/status/v1/logs?nivel=ERROR&limite=50` | Últimos registos (máx. 500) |

### Portal admin (`http://<ip-nas>:8089`)

| Página | O que permite |
|---|---|
| Estado | Gateway, token Tesla, clientes, pedidos/warnings/erros 24 h, testar ligação, renovar token |
| Clientes | Criar (mostra a chave uma vez), ativar/desativar, nova chave, apagar |
| Logs | Atividade, warnings e erros, com filtro por nível e cliente |
| Definições | Cache, retenção de logs, Status API (ativar/desativar/novo token), password |

Login definido com `tesla-setup admin`. Sessão de 8 h, formulários com proteção CSRF, tentativas de login
registadas nos logs.

### Logs

| Nível | Exemplos |
|---|---|
| INFO | Pedidos com sucesso, renovação de token, login, alterações de definições |
| WARNING | Chave inválida, cliente inativo, respostas 4xx, 429 da Tesla, `wake_up`, login falhado, chaves geradas |
| ERROR | Respostas 5xx, falha na renovação do token, falha de ligação à Tesla |

Retenção por omissão de 30 dias (limpeza no arranque e em Definições).

## Custo da Fleet API

| Item | Valor |
|---|---|
| Mensalidade | Nenhuma (pay-per-use) |
| Crédito mensal | 10 $ por conta (chega para uso pessoal de 1–2 carros) |
| Streaming signals | 150 000 por 1 $ |
| Comandos | 1 000 por 1 $ |
| APIs de energia | Grátis |

## Passos

### A. Contentor

1. Templates e chave SSH:
   ```bash
   cd /volume1/docker/tesla-key
   cp .env.example .env                       # ajustar SSH_USER, SSH_UID (id -u <utilizador>), portas, rede
   cp tesla/tesla.env.example tesla/tesla.env # preencher depois do passo 11
   # colar a chave pública SSH em ssh/authorized_keys
   ```
2. Arrancar:
   ```bash
   sudo docker compose up -d --build
   ```
3. Gerar as chaves Tesla, publicar a pública e configurar o gateway:
   ```bash
   ssh -p 2223 <utilizador>@<ip-nas>
   tesla-setup chaves
   tesla-setup admin                          # login do portal admin
   tesla-setup cliente add portal "Portal web"  # chave para o portal
   ```
4. Guardar uma cópia offline da `tesla/keys/private-key.pem` (é a que vai para o `tesla-http-proxy`).

### B. Rede e DSM

| # | Onde | O quê |
|---|---|---|
| 5 | DDNS | Alias `tesla` no domínio DDNS |
| 6 | Router | Port forward **80** e **443** → NAS |
| 7 | DSM → Segurança → Certificado | Let's Encrypt para `tesla.<dominio>` |
| 8 | DSM → Portal de Início de Sessão → Avançado → Proxy Reverso | `HTTPS tesla.<dominio>:443` → `HTTP localhost:8088` |
| 9 | DSM → Certificado → Configurações | Associar o certificado à regra do Proxy Reverso |

10. Testar em 4G: `https://tesla.<dominio>/.well-known/appspecific/com.tesla.3p.public-key.pem`
    deve mostrar `-----BEGIN PUBLIC KEY-----`.

### C. Tesla

11. Criar a aplicação em [developer.tesla.com](https://developer.tesla.com):
    - Grant types: *Authorization Code* + *Client Credentials*
    - Origin: `https://tesla.<dominio>`
    - Redirect URI: `http://localhost:8080/callback`
    - Scopes: `vehicle_device_data`, `vehicle_cmds`, `vehicle_charging_cmds`, `offline_access`

    Preencher `TESLA_DOMAIN`, `TESLA_CLIENT_ID` e `TESLA_CLIENT_SECRET` em `tesla/tesla.env`.
12. Dentro do contentor:
    ```bash
    tesla-setup verificar      # chave acessível pelo domínio
    tesla-setup partner        # registo de partner
    tesla-setup url            # abrir no browser, fazer login, copiar o code
    tesla-setup token <code>   # guarda tokens.json
    tesla-setup emparelhar     # abrir o link no telemóvel com a app Tesla
    tesla-setup veiculos       # testar
    ```

### D. Comandos

16. Para comandos (abrir, clima, carga) é preciso o `tesla-http-proxy`
    ([teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command)) noutro contentor, com a
    `private-key.pem`.

## Atualizar para uma nova versão

Copiar só os ficheiros do projeto (`Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `nginx.conf`,
`tesla-setup.sh`, `gateway/`, `*.example`) e **não** substituir:

| Manter | Porquê |
|---|---|
| `.env` | Utilizador, UID, portas e rede |
| `ssh/authorized_keys` | Chave SSH (o do projeto é só um comentário) |
| `tesla/` | `tesla.env`, chaves, tokens e `gateway.db` |
| `hostkeys/` | Host key SSH |

```bash
sudo docker compose up -d --build --force-recreate
sudo docker exec tesla-key grep -c gunicorn /entrypoint.sh   # > 0 confirma o entrypoint novo
```

## Resolução de problemas

| Sintoma | Causa | Solução |
|---|---|---|
| PuTTY: `No supported authentication methods available (server sent: publickey)` | `ssh/authorized_keys` vazio ou só com o comentário, ou o PuTTY sem o `.ppk` | Colar a chave pública (uma linha `ssh-ed25519 AAAA...`) e `docker restart tesla-key`; no PuTTY indicar o `.ppk` em Connection → SSH → Auth |
| `tesla-setup: command not found` | Comando corrido no NAS ou imagem antiga | Só existe dentro do contentor (SSH ou `docker exec -u <utilizador> tesla-key tesla-setup ...`); se faltar, rebuild |
| Portal admin não abre, `docker port` mostra `8001 -> 8089` | O `gunicorn` não arrancou | `docker exec tesla-key ps aux \| grep gunicorn`; se não houver, ver as linhas abaixo |
| `grep gunicorn /entrypoint.sh` sem resultado | `entrypoint.sh` antigo na imagem | Copiar o `entrypoint.sh` novo e `docker compose up -d --build --force-recreate` (ou `build --no-cache`) |
| `sqlite3.OperationalError: attempt to write a readonly database` | Ficheiros em `tesla/` criados como `root` (`docker exec` sem `-u`) ou UID diferente entre NAS e contentor | `SSH_UID` = `id -u <utilizador>` no `.env`, `sudo chown -R <utilizador>:users tesla`, rebuild |
| `tesla.env` ilegível no contentor | Dono no NAS com UID diferente do contentor | Igual à linha anterior |
| Gunicorn: `Control server error: [Errno 13] Permission denied` | Aviso do Gunicorn 25 (socket de controlo) | Não impede o arranque; pode ser ignorado |
| Teste manual do portal admin | — | `docker exec -u <utilizador> tesla-key gunicorn --chdir /opt/gateway --bind 127.0.0.1:8001 admin:app` (mostra o erro) |

Correr o `tesla-setup` sempre como o utilizador do contentor (SSH ou `docker exec -u <utilizador>`), nunca como
root. O arranque do contentor volta a acertar o dono de `/opt/tesla`.

## Notas

- SSH só com chave, utilizador definido em `SSH_USER` (por omissão `admin`), com `sudo` sem password; sem password de login e sem root. Para mudar de utilizador: alterar `SSH_USER`
  no `.env` e `docker compose up -d --build`.
- Alterações ao `authorized_keys` aplicam-se com `docker restart tesla-key`.
- Os volumes do Synology forçam permissões 0755; por isso a host key fica em `hostkeys/` mas o `sshd` usa uma
  cópia interna com `600 root:root`.
- A `private-key.pem` da Tesla fica em `tesla/keys/` (volume, fora do `html/`); nunca em `html/`. Não regenerar depois de emparelhar.
- O domínio tem de ser exatamente o mesmo na app, no registo de partner e no link de emparelhamento.
- `tesla.env`, `tokens.json`, `keys/` e `.env` estão no `.gitignore`.
- A porta 8000 nunca deve ser publicada nem passar pelo Proxy Reverso do DSM (só o `/.well-known` é público).
- O portal admin (8089) é só para a LAN; se o quiser fora de casa, use VPN em vez de abrir a porta.
- O gateway renova o token e guarda o novo `refresh_token` em `tokens.json`; não usar o mesmo refresh token noutro sítio, porque a Tesla roda-o a cada renovação.
- `DOCKER_NETWORK` tem de ser uma rede já existente (`docker network ls`) e partilhada com o portal.
- O token de partner e de utilizador usam `TESLA_TOKEN_URL` (`fleet-auth.prd.vn.cloud.tesla.com`); pode ser alterado em `tesla.env`.
