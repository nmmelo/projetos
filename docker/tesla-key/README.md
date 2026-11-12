# tesla-key

Contentor Ubuntu 26.04 com **nginx + SSH** para alojar, num Synology, a chave pública da Tesla Fleet API em
`https://tesla.cubiculo.giize.com/.well-known/appspecific/com.tesla.3p.public-key.pem`.

O nginx só responde a esse caminho; tudo o resto devolve 404. O acesso SSH (utilizador `nmmelo`) serve para
gerar e publicar a chave.

## Estrutura

```
tesla-key/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── nginx.conf
├── ssh/authorized_keys     # chave pública SSH de quem entra no contentor
├── html/.well-known/appspecific/com.tesla.3p.public-key.pem
└── hostkeys/               # criado no 1.º arranque (host key SSH persistente)
```

## Portas

| Porta NAS | Contentor | Uso |
|---|---|---|
| 8088 | 80 | nginx → destino do Proxy Reverso do DSM |
| 2223 | 22 | SSH (só LAN, não abrir no router) |

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

1. Colar a chave pública SSH em `ssh/authorized_keys`.
2. Arrancar:
   ```bash
   cd /volume1/docker/tesla-key
   sudo docker compose up -d --build
   ```
3. Gerar as chaves Tesla e publicar a pública:
   ```bash
   ssh -p 2223 nmmelo@<ip-nas>
   mkdir -p ~/tesla && cd ~/tesla
   openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
   openssl ec -in private-key.pem -pubout -out public-key.pem
   cp public-key.pem /var/www/html/.well-known/appspecific/com.tesla.3p.public-key.pem
   ```
4. Tirar a privada do contentor (a home não tem volume) e apagá-la lá dentro:
   ```bash
   scp -P 2223 nmmelo@<ip-nas>:tesla/private-key.pem .
   ```

### B. Rede e DSM

| # | Onde | O quê |
|---|---|---|
| 5 | Dynu | Alias `tesla` em `cubiculo.giize.com` |
| 6 | Router | Port forward **80** e **443** → NAS |
| 7 | DSM → Segurança → Certificado | Let's Encrypt para `tesla.cubiculo.giize.com` |
| 8 | DSM → Portal de Início de Sessão → Avançado → Proxy Reverso | `HTTPS tesla.cubiculo.giize.com:443` → `HTTP localhost:8088` |
| 9 | DSM → Certificado → Configurações | Associar o certificado à regra do Proxy Reverso |

10. Testar em 4G: `https://tesla.cubiculo.giize.com/.well-known/appspecific/com.tesla.3p.public-key.pem`
    deve mostrar `-----BEGIN PUBLIC KEY-----`.

### C. Tesla

11. Criar a aplicação em [developer.tesla.com](https://developer.tesla.com):
    - Grant types: *Authorization Code* + *Client Credentials*
    - Origin: `https://tesla.cubiculo.giize.com`
    - Redirect URI: `http://localhost:8080/callback`
    - Scopes: `vehicle_device_data`, `vehicle_cmds`, `vehicle_charging_cmds`, `offline_access`
12. Registo de partner (EU):
    ```bash
    CID=<client_id>; CSECRET=<client_secret>
    AUD=https://fleet-api.prd.eu.vn.cloud.tesla.com

    PARTNER_TOKEN=$(curl -s -X POST https://auth.tesla.com/oauth2/v3/token \
      -d grant_type=client_credentials -d client_id=$CID -d client_secret=$CSECRET \
      -d "scope=openid vehicle_device_data vehicle_cmds vehicle_charging_cmds" \
      -d audience=$AUD | jq -r .access_token)

    curl -X POST $AUD/api/1/partner_accounts \
      -H "Authorization: Bearer $PARTNER_TOKEN" -H "Content-Type: application/json" \
      -d '{"domain":"tesla.cubiculo.giize.com"}'
    ```
13. Token do utilizador (OAuth):
    ```
    https://auth.tesla.com/oauth2/v3/authorize?response_type=code&client_id=<CID>&redirect_uri=http://localhost:8080/callback&scope=openid%20offline_access%20vehicle_device_data%20vehicle_cmds%20vehicle_charging_cmds&state=abc
    ```
    Copiar o `code` do URL de redirect e trocar por tokens:
    ```bash
    curl -s -X POST https://auth.tesla.com/oauth2/v3/token \
      -d grant_type=authorization_code -d client_id=$CID -d client_secret=$CSECRET \
      -d code=<CODE> -d audience=$AUD \
      -d redirect_uri=http://localhost:8080/callback | jq
    ```
14. Emparelhar com o carro (telemóvel com a app Tesla): `https://tesla.com/_ak/tesla.cubiculo.giize.com`
15. Testar:
    ```bash
    curl -H "Authorization: Bearer <ACCESS_TOKEN>" $AUD/api/1/vehicles
    ```

### D. Comandos

16. Para comandos (abrir, clima, carga) é preciso o `tesla-http-proxy`
    ([teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command)) noutro contentor, com a
    `private-key.pem`.

## Notas

- SSH só com chave, utilizador `nmmelo` (sem password, sem root). Para mudar de utilizador: alterar `SSH_USER`
  no `docker-compose.yml` e `docker compose up -d --build`.
- Alterações ao `authorized_keys` aplicam-se com `docker restart tesla-key`.
- Os volumes do Synology forçam permissões 0755; por isso a host key fica em `hostkeys/` mas o `sshd` usa uma
  cópia interna com `600 root:root`.
- A `private-key.pem` da Tesla **nunca** fica em `html/` nem neste contentor.
- O domínio tem de ser exatamente o mesmo na app, no registo de partner e no link de emparelhamento.
