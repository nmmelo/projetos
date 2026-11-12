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
> eu tenho o dominio do ddns cubiculo.giize.com
> neste caso vou criar o tesla.cubiculo.giize.com

**Resposta:**
- Alias `tesla` no Dynu, certificado Let's Encrypt no DSM (porta 80 aberta), porta 443 para o NAS.
- O domínio `tesla.cubiculo.giize.com` tem de ser igual na app, no registo de partner e no link
  `https://tesla.com/_ak/tesla.cubiculo.giize.com`.

---

## 5. Contentor para a chave

**Perguntas:**
> posso criar um container para guardar essa chave associado depois no DSM o tesle.cubiculo.giize.com a uma porta especifica
> quero o docker-compose com sessão ssh com import de uma chave publica no autorikey, como já fiz com outros exemplos, pode ser a imagem ubuntu 26.04
> eu queria outro utilizador para entrar como ssh, por exemplo nmmelo

**Resposta:**
Contentor Ubuntu 26.04 com nginx (porta 8088) e SSH (porta 2223, utilizador `nmmelo`, só chave). O Proxy
Reverso do DSM liga `HTTPS tesla.cubiculo.giize.com:443` → `HTTP localhost:8088`. A Tesla lê sempre na 443;
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
