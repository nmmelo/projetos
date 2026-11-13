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
├── atualizar.sh            # atualização a partir do repositório (sem tocar nos dados)
├── atualizar.lista         # ficheiros atualizados pelo atualizar.sh
├── tesla-setup.sh          # instalado como /usr/local/bin/tesla-setup
├── gateway/
│   ├── common.py           # base de dados, clientes, tokens Tesla, logs
│   ├── gateway.py          # API de clientes + Status API (porta 8000, rede Docker)
│   ├── imagens.py          # imagem do carro (compositor do configurador Tesla)
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
│   ├── imagens/            # imagens do carro em cache
│   └── gateway.db          # SQLite: clientes, definições, login, logs (não vai para o Git)
└── hostkeys/               # criado no 1.º arranque (host key SSH persistente)
```

## Templates

| Ficheiro | Copiar para | Variáveis |
|---|---|---|
| `.env.example` | `.env` | `SSH_USER`, `SSH_UID`, `HTTP_PORT`, `SSH_PORT`, `CONTAINER_NAME`, `DATA_DIR`, `ADMIN_PORT`, `DOCKER_NETWORK`, `UPDATE_URL` |
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
| `tesla-setup cliente add NOME [DESCRIÇÃO] [--perm LISTA]` | Cria um cliente e mostra a chave (só uma vez); sem `--perm` usa o preset `leitura` |
| `tesla-setup cliente list` | Lista os clientes (prefixo, estado, último uso, pedidos, permissões) |
| `tesla-setup cliente perm NOME [set\|add\|rm LISTA]` | Mostra ou altera as permissões do cliente |
| `tesla-setup permissoes` | Lista as permissões e os presets |
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
| GET | `/api/me` | Nome e permissões do cliente |
| GET | `/api/token/status` | Validade do access token |
| GET | `/api/vehicles` | Lista de veículos |
| GET | `/api/vehicles/<vin>` | Estado (`online`/`asleep`), não acorda o carro |
| GET | `/api/vehicles/<vin>/data` | `vehicle_data` com cache (`?endpoints=charge_state;drive_state`, `?fresh=1`) |
| GET | `/api/vehicles/<vin>/location` | Latitude, longitude, direção, velocidade (scope `vehicle_location`) |
| GET | `/api/vehicles/<vin>/image` | Imagem do modelo com a cor/jantes do carro (`?view=STUD_3QTR&size=800`, `?formato=url`) |
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

### Permissões por cliente

Cada cliente só acede ao que lhe for atribuído; o resto devolve 403 (registado nos logs como WARNING).
Clientes que já existiam antes desta funcionalidade ficam com `tudo`; novos clientes ficam com `leitura`.

| Permissão | Dá acesso a | Endpoints |
|---|---|---|
| `veiculos` | Lista e estado dos veículos (online/asleep) | `/api/vehicles`, `/api/vehicles/{vin}` |
| `carga` | Bateria, autonomia, carregamento (`charge_state`) | `/data` |
| `clima` | Temperaturas e climatização (`climate_state`) | `/data` |
| `conducao` | Mudança, velocidade, potência, sem coordenadas (`drive_state`) | `/data` |
| `estado` | Odómetro, trancas, sentinela, versão (`vehicle_state`) | `/data` |
| `config` | Modelo, cor e jantes (`vehicle_config`) | `/data` |
| `localizacao` | Coordenadas GPS (`location_data`) | `/location`, `/data` |
| `imagem` | Imagem do modelo | `/image` |
| `token` | Estado do token Tesla | `/api/token/status` |
| `acordar` | Acordar o carro (sensível) | `/wake` |
| `fleet` | Passthrough para qualquer GET da Fleet API (sensível, acesso amplo) | `/api/fleet/...` |

| Preset | Permissões |
|---|---|
| `tudo` | Todas |
| `leitura` | Todas menos `acordar` e `fleet` (omissão para novos clientes) |
| `basico` | `veiculos`, `carga`, `imagem` |
| `nenhuma` | Nenhuma (só `/api/health` e `/api/me`) |

```bash
tesla-setup cliente add ecra "Ecrã da garagem" --perm basico
tesla-setup cliente add mapa --perm veiculos,localizacao,imagem
tesla-setup cliente perm portal                       # ver
tesla-setup cliente perm portal set leitura           # substituir
tesla-setup cliente perm portal add acordar           # acrescentar
tesla-setup cliente perm portal rm localizacao,fleet  # retirar
```
No portal admin: Clientes → **Permissões** (caixas de seleção e presets); o preset também se escolhe ao criar.

### Ativar a localização

A localização exige o scope `vehicle_location`, que tem de existir na aplicação e no token.

1. developer.tesla.com → a aplicação → scopes → ativar **Vehicle Location**.
2. Em `tesla/tesla.env`, `TESLA_SCOPES` tem de incluir `vehicle_location` (já está no `tesla.env.example`).
3. Refazer o OAuth dentro do contentor (os tokens antigos não têm o novo scope):
   ```bash
   tesla-setup url
   tesla-setup token '<url de redirect>'
   ```
4. Testar: `curl -H "X-API-Key: tk_..." http://tesla-key:8000/api/vehicles/<VIN>/location`

### Imagem do carro

Render do configurador Tesla (compositor em `static-assets.tesla.com`), não as câmaras. O compositor desenha o
carro a partir de uma lista de **códigos de opção** (modelo, pintura, jantes, interior). É essa lista que se
põe em `TESLA_IMAGE_OPTIONS`.

| Código (exemplos) | Significado |
|---|---|
| `MT3..` / `MDL3` | Modelo e versão (Model 3) |
| `PPSW`, `PBSB`, `PN01`, `PN00`, `PR01`, `PMNG`, `PPSB` | Pintura (Pearl White, Solid Black, Stealth Grey, Quicksilver, Ultra Red, Midnight Silver, Deep Blue) |
| `W38B`, `W39B`, `W38A`, `W39A` | Jantes |
| `IPB..`, `IBB..` | Interior (vista `STUD_SEAT`) |

Obter os códigos exatos do carro:

1. tesla.com → conta → o carro → botão direito na imagem → **copiar endereço da imagem**.
2. O endereço tem a forma `.../compositor/?model=m3&view=STUD_3QTR&size=...&options=$MT353,$PPSW,$W38A,$IPB3,...`
3. Copiar o valor de `options=` para `tesla/tesla.env`, **sem os `$`** (o `tesla-setup` lê o ficheiro em bash
   e o `$` seria interpretado), ou entre plicas:
   ```bash
   TESLA_IMAGE_MODEL=m3
   TESLA_IMAGE_OPTIONS=MT353,PPSW,W38A,IPB3
   # ou: TESLA_IMAGE_OPTIONS='$MT353,$PPSW,$W38A,$IPB3'
   ```
4. Gerar de novo: `.../image?fresh=1` (ou apagar `tesla/imagens/`). Para ver o URL usado: `.../image?formato=url`.

Sem `TESLA_IMAGE_OPTIONS`, o gateway monta os códigos a partir de `exterior_color` e `wheel_type` do
`vehicle_config` (aproximado; cores ou jantes desconhecidas ficam com o aspeto por omissão e geram um WARNING).
As imagens ficam em `tesla/imagens/` durante `TESLA_IMAGE_CACHE_DIAS` (30).

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
| Clientes | Criar com preset de permissões (mostra a chave uma vez), editar permissões, ativar/desativar, nova chave, apagar |
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

O `atualizar.sh` descarrega do repositório os ficheiros do projeto listados em `atualizar.lista`, mostra o
que mudou, guarda os ficheiros antigos em `.backup/<data>/` e faz o rebuild. **Nunca** toca em:

| Mantido | Porquê |
|---|---|
| `.env` | Utilizador, UID, portas, rede, `UPDATE_URL` |
| `ssh/authorized_keys` | Chaves SSH |
| `tesla/tesla.env`, `tesla/keys/`, `tesla/tokens.json`, `tesla/gateway.db`, `tesla/imagens/` | Configuração, chaves, tokens, clientes, logs |
| `hostkeys/`, `html/` | Host key SSH e chave pública publicada |

1. Uma vez: definir no `.env` a origem (URL "raw" da pasta `tesla-key` no repositório):
   ```bash
   UPDATE_URL=https://raw.githubusercontent.com/<utilizador>/<repositorio>/main/docker/tesla-key
   ```
2. Atualizar:
   ```bash
   cd /volume1/docker/tesla-key
   ./atualizar.sh --simular     # só mostra o que mudaria
   ./atualizar.sh               # atualiza, faz backup e rebuild
   ./atualizar.sh --sem-build   # atualiza sem rebuild
   ```

No fim avisa se o `.env.example` ou o `tesla.env.example` têm variáveis que ainda não estão na configuração
real (ficam com o valor por omissão até as acrescentar). O próprio `atualizar.sh` também se atualiza.

Primeira vez (se ainda não tiver o `atualizar.sh`):
```bash
curl -fsSL "$UPDATE_URL/atualizar.sh" -o atualizar.sh && chmod +x atualizar.sh
```

## Instalar como stack do Portainer (a partir do Git)

Alternativa ao `docker compose` + `atualizar.sh` no NAS: o Portainer clona o repositório, faz o build e
atualiza com **Pull and redeploy**, e as variáveis do `.env` passam a ser editadas na interface.

Os dados continuam numa pasta do NAS (`DATA_DIR`), por isso não se perdem nos redeploys.

1. **Pasta de dados no NAS** (a que já existe serve), com `ssh/authorized_keys`, `tesla/tesla.env`, etc.
2. **Remover o contentor criado pelo `docker compose`** (os dados ficam na pasta):
   Portainer → Containers → `tesla-key` → Stop → Remove (ou `sudo docker compose down` no NAS).
3. **Stacks → Add stack**:

   | Campo | Valor |
   |---|---|
   | Name | `tesla-key` |
   | Build method | **Repository** |
   | Repository URL | `https://github.com/<utilizador>/<repositorio>` |
   | Repository reference | `refs/heads/main` |
   | Compose path | `docker/tesla-key/docker-compose.yml` |
   | Automatic updates (opcional) | Polling (ex.: 24h) ou Webhook |

4. **Environment variables** → *Advanced mode*, colar e ajustar:
   ```
   SSH_USER=<utilizador>
   SSH_UID=<id -u do utilizador no NAS>
   SSH_PORT=2223
   HTTP_PORT=8088
   ADMIN_PORT=8089
   CONTAINER_NAME=tesla-key
   DOCKER_NETWORK=<rede partilhada>
   DATA_DIR=/volume1/docker/tesla-key
   ```
   `DATA_DIR` tem de ser um **caminho absoluto**: com `.` os dados iriam para a cópia do repositório que o
   Portainer guarda internamente.
5. **Deploy the stack** e ver Containers → `tesla-key` → Logs (devem aparecer os dois `gunicorn`).

| Tarefa | Onde |
|---|---|
| Atualizar para a versão do repositório | Stacks → `tesla-key` → **Pull and redeploy** (o `pull_policy: build` força o rebuild) |
| Mudar portas, utilizador, rede | Stacks → `tesla-key` → Environment variables → Update the stack |
| `tesla-setup` | Containers → `tesla-key` → Console → *User* `<utilizador>` → `/bin/bash` |
| Logs, restart | Containers → `tesla-key` |

Neste modo o `.env` e o `atualizar.sh` do NAS não são usados. Não misturar os dois modos no mesmo contentor.
Se o Portainer não aceitar `pull_policy: build` (versões antigas), atualizar o Portainer.

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
