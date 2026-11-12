# Conversa: tesla-key (Tesla Fleet API no Synology)

Chat de 12 de novembro de 2026. As perguntas estão como foram escritas; as respostas estão resumidas, mas
mantêm os comandos e as conclusões. Os ficheiros finais estão nesta pasta.

---

## 1. Custo da API da Tesla

**Pergunta:**
> consigo aceder ao meu tesla pela api da tesla, tem algum custo

**Resposta:**
Sim, pela Fleet API (developer.tesla.com), em modelo pay-per-use com crédito mensal de 10 $ por conta.

| Item | Preço |
|---|---|
| Streaming signals | 150 000 por 1 $ |
| Comandos | 1 000 por 1 $ |
| APIs de energia | Grátis |
| Crédito mensal | 10 $ |

---

## 2. Mensalidade

**Pergunta:**
> mas tenho que pagar alguma mensalidade?

**Resposta:**
Não. Só se paga o que passar dos 10 $/mês. Serviços de terceiros (Teslemetry, Teslascope, MyTeslamate) é que
têm mensalidade própria.

---

## 3. Passos gerais

**Pergunta:**
> que passos preciso de fazer

**Resposta:**
Par de chaves EC (prime256v1) → publicar a pública em `/.well-known/appspecific/com.tesla.3p.public-key.pem`
→ criar a app no portal → registo de partner (região EU) → OAuth → emparelhar a chave virtual no carro →
`tesla-http-proxy` para comandos assinados → testar `/api/1/vehicles`.

---

## 4. Synology e domínio

**Perguntas:**
> e se tiver um synology.
> eu tenho o dominio do ddns <dominio>
> neste caso vou criar o tesla.<dominio>

**Resposta:**
- Alias `tesla` no DDNS, certificado Let's Encrypt no DSM (porta 80 aberta), porta 443 para o NAS.
- O domínio `tesla.<dominio>` tem de ser igual na app, no registo de partner e no link
  `https://tesla.com/_ak/tesla.<dominio>`.

---

## 5. Contentor para a chave

**Perguntas:**
> posso criar um container para guardar essa chave associado depois no DSM o tesla.<dominio> a uma porta especifica
> quero o docker-compose com sessão ssh com import de uma chave publica no autorikey, como já fiz com outros exemplos, pode ser a imagem ubuntu 26.04
> eu queria outro utilizador para entrar como ssh, por exemplo <utilizador>

**Resposta:**
Contentor Ubuntu 26.04 com nginx (porta 8088) e SSH (porta 2223, utilizador `<utilizador>`, só chave). O Proxy
Reverso do DSM liga `HTTPS tesla.<dominio>:443` → `HTTP localhost:8088`. A Tesla lê sempre na 443;
a porta específica é só interna.

---

## 6. Gerar a chave dentro do contentor

**Pergunta:**
> mas depois lá dentro gero a public key para a api da tesla?

**Resposta:**
Sim, na home (nunca em `/var/www/html`):
```bash
mkdir -p ~/tesla && cd ~/tesla
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
cp public-key.pem /var/www/html/.well-known/appspecific/com.tesla.3p.public-key.pem
```
A privada sai logo do contentor (a home não tem volume) e vai para o `tesla-http-proxy`. Não regenerar
depois de emparelhar.

---

## 7. Erro de permissões da host key

**Pergunta:**
> ao arrancar dá este erro, Permissions 0755 for '/etc/ssh/keys/ssh_host_ed25519_key' are too open.

**Resposta:**
As ACLs dos volumes do Synology forçam 0755. O `entrypoint.sh` passou a copiar a host key do volume para
`/etc/ssh/ssh_host_ed25519_key` com `600 root:root`, e o `sshd` usa essa cópia. Rebuild:
```bash
sudo docker compose down
sudo docker compose up -d --build
```

---

## 8. Sudo no utilizador SSH

**Pergunta:**
> no container não tenho sudo no <utilizador>

**Resposta:**
O `Dockerfile` passou a instalar o `sudo` e a criar `/etc/sudoers.d/<utilizador>` com `NOPASSWD:ALL`. É preciso
rebuild (`docker compose up -d --build`). O utilizador define-se em `SSH_USER` no `docker-compose.yml`.

---

## 9. Templates

**Pergunta:**
> podes gerar isto para colocar no container como template

**Resposta:**
- `.env.example` → `.env`: utilizador SSH, UID e portas, usados pelo `docker-compose.yml`.
- `tesla/tesla.env.example` → `tesla/tesla.env`: domínio, client id/secret e região, montado em `/opt/tesla`.
- `tesla-setup.sh`, instalado como `/usr/local/bin/tesla-setup`, com os passos `chaves`, `verificar`,
  `partner`, `url`, `token`, `emparelhar`, `veiculos`, `refresh` e `estado`. A imagem passou a ter `curl` e `jq`.

---

## 10. Gateway para outro projeto

**Perguntas:**
> eu não quero usar agora este container, apenas para validar, o que quero agora é ir buscar dados do meu tesla para outro projeto
> ou posso usar este container e o outro projeto pede a este os pedidos tipo proxy?
> o outro projeto está na porta interna 8020 e o tesla-key está na 8088 ... já coloquei ambos container na mesma network do docker

**Resposta:**
O `tesla-key` passou a ter um gateway (Flask + Gunicorn) na porta 8000, só na rede Docker, sem publish.
Guarda os segredos e os tokens, renova o access token (e o refresh token rodado), faz cache do
`vehicle_data` e exige `X-API-Key`. O portal chama `http://tesla-key:8000/api/...`. As portas 8088/8020 são
as do anfitrião; dentro da rede Docker usam-se o nome do contentor e a porta interna.
