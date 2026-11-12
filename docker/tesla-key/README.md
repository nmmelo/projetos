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
├── tesla-setup.sh          # instalado como /usr/local/bin/tesla-setup
├── gateway/
│   ├── gateway.py          # API interna (Flask + Gunicorn, porta 8000)
│   └── cliente_exemplo.py  # exemplo de cliente para o portal
├── ssh/authorized_keys     # chave pública SSH de quem entra no contentor
├── html/.well-known/appspecific/com.tesla.3p.public-key.pem
├── tesla/                  # montado em /opt/tesla
│   ├── tesla.env.example   # template da Tesla (domínio, client id/secret, região)
│   ├── tesla.env           # cópia preenchida (não vai para o Git)
│   ├── keys/               # private-key.pem / public-key.pem (não vai para o Git)
│   └── tokens.json         # access/refresh token (não vai para o Git)
└── hostkeys/               # criado no 1.º arranque (host key SSH persistente)
```

## Templates

| Ficheiro | Copiar para | Variáveis |
|---|---|---|
| `.env.example` | `.env` | `SSH_USER`, `SSH_UID`, `HTTP_PORT`, `SSH_PORT`, `CONTAINER_NAME`, `DOCKER_NETWORK`, `TESLA_GATEWAY_API_KEY`, `TESLA_GATEWAY_CACHE` |
| `tesla/tesla.env.example` | `tesla/tesla.env` | `TESLA_DOMAIN`, `TESLA_CLIENT_ID`, `TESLA_CLIENT_SECRET`, `TESLA_REGION`, `TESLA_REDIRECT_URI`, `TESLA_SCOPES` |

## Portas

| Porta NAS (`.env`) | Contentor | Uso |
|---|---|---|
| `HTTP_PORT` (8088) | 80 | nginx → destino do Proxy Reverso do DSM |
| `SSH_PORT` (2223) | 22 | SSH (só LAN, não abrir no router) |
| — (sem publish) | 8000 | Gateway API, só na rede Docker (`http://tesla-key:8000`) |

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

## Gateway API (para o portal)

```
Portal ──HTTP + X-API-Key──► tesla-key:8000 ──► Tesla Fleet API
                             (renova tokens, cache)
```

| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/health` | Sem autenticação |
| GET | `/api/token/status` | Validade do access token |
| POST | `/api/token/refresh` | Força a renovação |
| GET | `/api/vehicles` | Lista de veículos |
| GET | `/api/vehicles/<vin>` | Estado (`online`/`asleep`), não acorda o carro |
| GET | `/api/vehicles/<vin>/data` | `vehicle_data` com cache (`?endpoints=charge_state;drive_state`, `?fresh=1`) |
| POST | `/api/vehicles/<vin>/wake` | Acorda o carro (mais caro, gasta bateria) |
| GET | `/api/fleet/<caminho>` | Passthrough só de leitura para `/api/1/<caminho>` |

Todas (menos `health`) exigem o header `X-API-Key: <TESLA_GATEWAY_API_KEY>`. Com o carro a dormir,
`/data` devolve **408**.

No portal (mesma rede Docker):
```bash
TESLA_GATEWAY_URL=http://tesla-key:8000
TESLA_GATEWAY_API_KEY=<a mesma chave>
```
```bash
# teste a partir do contentor do portal
curl -H "X-API-Key: $TESLA_GATEWAY_API_KEY" http://tesla-key:8000/api/vehicles
```
Exemplo em Python: `gateway/cliente_exemplo.py`.

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
   cp .env.example .env                       # ajustar SSH_USER, portas
   cp tesla/tesla.env.example tesla/tesla.env # preencher depois do passo 11
   # colar a chave pública SSH em ssh/authorized_keys
   ```
2. Arrancar:
   ```bash
   sudo docker compose up -d --build
   ```
3. Gerar as chaves Tesla e publicar a pública:
   ```bash
   ssh -p 2223 <utilizador>@<ip-nas>
   tesla-setup chaves
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
- O gateway renova o token e guarda o novo `refresh_token` em `tokens.json`; não usar o mesmo refresh token noutro sítio, porque a Tesla roda-o a cada renovação.
- `DOCKER_NETWORK` tem de ser uma rede já existente (`docker network ls`) e partilhada com o portal.
- O token de partner e de utilizador usam `TESLA_TOKEN_URL` (`fleet-auth.prd.vn.cloud.tesla.com`); pode ser alterado em `tesla.env`.
