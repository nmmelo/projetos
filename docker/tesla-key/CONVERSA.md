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

---

## 11. Vários clientes, portal admin e Status API

**Pergunta:**
> para facilitar, pode ser adicionado mais clientes para aceder ao tesla-key, sempre interno, queria também que o setup permita configurar isso e fazer a gestão, mas também podia ter um pequeno portal web simples onde possa ver o estado do gateway, pode ter um login simples, que é definido no script setup, permite gerir o gateway, ter logs de atividade e erros ou warnings e também permitir no futuro ativar uma API, ou seja eu apartir de outro portal conseguir ter informação do gateway apensa, podendo para esse caso gerar um token para criar mais segurança entre o gateway e o outro

**Resposta:**
- Clientes com chave própria (`X-API-Key`), guardada em hash em `/opt/tesla/gateway.db` (SQLite), mostrada só
  na criação; ativar/desativar, nova chave e apagar.
- `tesla-setup admin | cliente ... | status-api ... | definicao | logs | logs-limpar | resumo`.
- Portal admin (Flask, porta 8001 → `ADMIN_PORT` 8089, só LAN): Estado, Clientes, Logs, Definições, com login,
  sessão de 8 h e CSRF.
- Logs INFO/WARNING/ERROR de pedidos, autenticação, renovação de token, erros da Tesla e ações de admin, com
  retenção configurável.
- Status API `/status/v1/` (health, resumo, clientes, logs) desativada por omissão, com token Bearer próprio
  que se pode regenerar; não dá acesso aos dados do carro.
- `TESLA_GATEWAY_API_KEY` do `.env` deixou de existir (substituída pelos clientes).

---

## 12. Instalação da nova versão: problemas e correções

**Perguntas (resumo):**
> ao fazer login com o meu user dá erro, por ssh No supported authentication methods available (server sent: publickey)
> não tinha colocado a minha chave publica no auto
> mas onde está o setup
> não consigo aceder via web
> ... sqlite3.OperationalError: attempt to write a readonly database

**Respostas:**
| Problema | Causa | Correção |
|---|---|---|
| SSH recusado | `ssh/authorized_keys` só com o comentário do projeto | Colar a chave pública e `docker restart tesla-key` |
| `tesla-setup` não encontrado | Procurado no NAS | Só existe dentro do contentor (SSH ou `docker exec -u <utilizador>`) |
| Portal admin sem resposta | `gateway.db`, `keys/` e `tokens.json` criados como `root`; `tesla.env` com o UID do NAS (1026) diferente do contentor (1001) | `SSH_UID=1026` no `.env`, `chown -R <utilizador>:users tesla`, rebuild |
| Continuava sem resposta | `entrypoint.sh` antigo no NAS (sem o arranque do `gunicorn`) | `entrypoint.sh` novo e `docker compose up -d --build --force-recreate` |

O `entrypoint.sh` passou a fazer `cd /opt/gateway` antes de arrancar o `gunicorn`. O README ganhou as secções
"Atualizar para uma nova versão" e "Resolução de problemas".

---

## 13. Referência da API para outros projetos

**Pergunta:**
> estou a comunicar com o tesla-key e dá este erro, Teste ao gateway: o gateway não tem este recurso (404) ... deve ser os endpoints, ele não sabe o que esperar

**Resposta:**
O 404 vinha de um caminho que o gateway não tem (a Status API estava desativada). Foram criados `API.md` e
`openapi.yaml` com os endpoints, headers, parâmetros, exemplos de resposta, unidades, códigos de erro e boas
práticas, para entregar a quem desenvolve o cliente. A chave do cliente (`tk_`, dados do carro) e o token da
Status API (`ts_`, estado do gateway) são credenciais diferentes.

---

## 14. Localização e imagem do carro

**Pergunta:**
> preciso de acrescentar no tesla-key a opção de localização do tesla neste caso, tb é possivel fornecer a imagem do carro, ou seja a imagem do tesla m3, não das camaras

**Resposta:**
- `GET /api/vehicles/<vin>/location`: latitude, longitude, direção, velocidade em km/h, mudança, hora do GPS
  e link OpenStreetMap. Requer o scope `vehicle_location` na aplicação Tesla e um novo OAuth
  (`tesla-setup url` + `tesla-setup token`). Sem coordenadas devolve 403.
- `GET /api/vehicles/<vin>/image`: render do configurador Tesla (compositor `static-assets.tesla.com`) com a
  cor e as jantes do carro, guardado em `tesla/imagens/`. Vistas `STUD_3QTR`, `STUD_SIDE`, `STUD_REAR`,
  `STUD_FRONT`, `STUD_SEAT`; `?formato=url` devolve só o URL. Códigos exatos em `TESLA_IMAGE_OPTIONS`
  (copiados da imagem do carro na conta tesla.com, sem `$`); sem eles, aproximação pela cor e jantes do
  `vehicle_config`.
- `TESLA_SCOPES` passou a incluir `vehicle_location`; `API.md` e `openapi.yaml` atualizados.

---

## 15. Permissões por cliente

**Pergunta:**
> podemos selecionar o que quero colocar na api para dar ao cliente, até podemos associar por cliente ou seja por token, assim alguns clientes podem ter acesso a tudo e outros a menos coisas, isto pode ser feito via web ou então via cli

**Resposta:**
- Permissões por cliente: `veiculos`, `carga`, `clima`, `conducao`, `estado`, `config`, `localizacao`, `imagem`,
  `token`, `acordar`, `fleet`; presets `tudo`, `leitura` (omissão), `basico`, `nenhuma`.
- Endpoint sem permissão → 403 (WARNING nos logs). Em `/data` as secções sem permissão são retiradas do
  pedido à Tesla e indicadas em `seccoes_negadas`.
- `GET /api/me` devolve o nome e as permissões do cliente.
- CLI: `cliente add ... --perm LISTA`, `cliente perm NOME [set|add|rm LISTA]`, `permissoes`.
- Portal admin: preset ao criar e página de permissões por cliente com caixas de seleção.
- Migração automática: clientes existentes ficam com `tudo`.

---

## 16. Atualizar sem copiar ficheiros e códigos da imagem

**Perguntas:**
> O que foi alterado para fazer echo no tesla-key sem ter de colocar lá os ficheiros
> Como funciona TESLA_IMAGE_OPTIONS e podes criar atualizar

**Resposta:**
- `atualizar.sh` + `atualizar.lista`: descarrega do repositório (`UPDATE_URL` no `.env`) os ficheiros do projeto,
  mostra novos/alterados, faz backup em `.backup/<data>/`, rebuild e verifica `/api/health`. Nunca toca em
  `.env`, `ssh/`, `tesla/tesla.env`, chaves, tokens, `gateway.db`, imagens, `hostkeys/` e `html/`. Opções
  `--simular` e `--sem-build`; avisa de variáveis novas nos modelos `.example`.
- `TESLA_IMAGE_OPTIONS`: códigos de opção do compositor Tesla (modelo, pintura, jantes, interior), copiados do
  parâmetro `options=` da imagem do carro na conta tesla.com, sem `$`.

---

## 17. Gestão pelo Portainer

**Perguntas:**
> e se eu não tiver acesso ao nas neste momento, consigo fazer alterações no container?
> mas eu tenho acesso ao container pelo portainer ... como faço isso

**Resposta:**
- Sem acesso ao NAS: clientes, permissões, Status API, logs, token e `tesla.env` geríveis pelo portal admin, por
  SSH no contentor ou pela consola do Portainer (sempre como o utilizador do contentor, nunca root).
- Stack do Portainer a partir do Git: `docker-compose.yml` passou a ter `DATA_DIR` (pasta absoluta dos dados no
  NAS) e `pull_policy: build`; atualização com "Pull and redeploy" e variáveis na interface do Portainer.

---

## 18. Portal para telemóvel, tesla.env, tempos de atualização e consumo

**Pergunta:**
> permitir que a interface web do tesla-key esteja adaptavel para mobile ... ver a informação do que está no tesla.env ... alterar o tempo em que é feito o refrescamento da imagem e de outros dados ... definir o que podemos ir buscar ou o que estamos a ir buscar ... ter uma noção de quantos creditos já foram usados no mês corrente

**Resposta:**
- Portal admin responsivo (menu deslizante, cartões em 2 colunas, tabelas com deslocamento, formulários em coluna).
- Página **Tesla**: ver/editar o `tesla.env` (Client Secret mascarado, só substituído se for escrito um novo),
  scopes com caixas e comparação "pedido" vs "no token atual".
- **Definições**: tempos de cache por tipo (dados, localização, veículos, imagem), secções do `/data` por
  omissão, funcionalidades ativas no gateway (403 para todos quando desligadas), preços e crédito.
- Página **Consumo**: estimativa do mês por tipo, cliente, endpoint e dia, com limites diários; KPI no Estado.
  Os pedidos à Tesla são registados na tabela `consumo` do `gateway.db`.
- CLI: `tesla-setup consumo`, `funcionalidades`, `config` e mais chaves em `definicao`.
