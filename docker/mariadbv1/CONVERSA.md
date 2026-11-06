# Conversa: MariaDB docker-compose

Chat com o Claude, a 6 de novembro de 2026. As perguntas estão como foram escritas; as respostas
estão resumidas, mas mantêm os comandos e as conclusões. Os ficheiros finais estão nesta pasta.

---

## 1. Criar a MariaDB em docker-compose

**Pergunta:**
> quero um docker-compose para criar uma mariadb na porta default, com um user admin com permissões de dba e que consiga aceder do ip 10.10.101.25, o volume dos dados ficam guardados no mount com o nome de data e outro mount com o nome de conf onde temos as configurações da mariadb

**Resposta:**
Foram criados `docker-compose.yml`, `.env`, `conf/custom.cnf` e `init/01-admin.sh`.

| Item | Valor |
|---|---|
| Imagem | `mariadb:11.4` |
| Porta | `3306:3306` |
| Utilizador | `admin@10.10.101.25` com `ALL PRIVILEGES ON *.* WITH GRANT OPTION` |
| Root | Só `localhost` (dentro do contentor) |
| `./data` | `/var/lib/mysql` |
| `./conf` | `/etc/mysql/conf.d` (só leitura) |

Notas:
- Foi acrescentada uma terceira pasta, `init`, com o script que cria o user admin restrito ao IP.
  Só corre no 1.º arranque, com `data/` vazia.
- Teste a partir de 10.10.101.25: `mariadb -h <ip_servidor> -u admin -p`

---

## 2. Pasta do Docker a ocupar muito espaço

**Pergunta:**
> tenho uma pasta com muito espaço, usado pelo portainer, contudo não sei de que container é, /docker/containers/482730ff80a3240c9fd4e8adcf173eb0ee93be87562c39b25da5189abbc90f7a

**Resposta:**
O nome da pasta é o ID completo do contentor:

```bash
docker ps -a --no-trunc | grep 482730ff80a3
docker inspect --format '{{.Name}}' 482730ff80a3
```

Normalmente o espaço é ocupado pelo log `*-json.log`:

```bash
du -sh /docker/containers/482730ff80a3*/*
docker inspect --format '{{.LogPath}}' 482730ff80a3
```

| Ação | Comando |
|---|---|
| Esvaziar o log agora | `truncate -s 0 $(docker inspect --format '{{.LogPath}}' 482730ff80a3)` |
| Limitar um contentor (compose) | bloco `logging:` no serviço |
| Limitar todos | `/etc/docker/daemon.json`: `{"log-driver":"json-file","log-opts":{"max-size":"50m","max-file":"3"}}` + `systemctl restart docker` |

Notas:
- Não apagar o log com `rm` com o contentor a correr: o espaço só é libertado quando o contentor
  reinicia. Usar `truncate`.
- O `daemon.json` só se aplica a contentores criados depois (`--force-recreate`).
- Se o `docker ps -a` não devolver nada, a pasta é órfã e pode ser apagada com o Docker parado.

---

## 3. Onde colocar o bloco `logging`

**Pergunta:**
> onde coloco o logging: { driver: json-file, options: { max-size: "50m", max-file: "3" } }

**Resposta:**
Dentro do serviço, ao mesmo nível de `image`, `ports` e `volumes`:

```yaml
services:
  mariadb:
    image: mariadb:11.4
    ports:
      - "3306:3306"
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
```

Aplicar com `docker compose up -d --force-recreate`.

| Onde o contentor foi criado | Onde se define |
|---|---|
| Ficheiro compose | Bloco `logging:` no serviço |
| Stack do Portainer | O mesmo bloco no editor da stack → **Update the stack** |
| Contentor criado à mão no Portainer | **Duplicate/Edit** → separador **Logging** → `max-size=50m`, `max-file=3` → **Deploy** |

---

## 4. Erro `logging.options must be a mapping`

**Pergunta:**
> validating /docker/services/gitlab/docker-compose.yml: services.web.logging.options must be a mapping

**Resposta:**
As `options` não estavam escritas como pares `chave: valor`.

| ❌ Errado | Problema |
|---|---|
| `- max-size: "50m"` | Hífen: o YAML lê como lista |
| `max-size=50m` | `=` em vez de `:` |
| `max-size:"50m"` | Falta o espaço depois de `:` |

Forma correta:

```yaml
services:
  web:
    image: gitlab/gitlab-ce:latest
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
```

Validar com `docker compose config -q && docker compose up -d --force-recreate`. Usar só espaços,
sem tabs.

---

## 5. Password com hash no `.env`

**Pergunta:**
> podemos no .env colocar a password com hash?

**Resposta:**
Sim, com hashes no formato `mysql_native_password`.

| Ficheiro | Alteração |
|---|---|
| `.env` | `MARIADB_ROOT_PASSWORD_HASH` e `MARIADB_ADMIN_PASSWORD_HASH` |
| `docker-compose.yml` | Passa as variáveis `*_HASH` |
| `init/01-admin.sh` | `IDENTIFIED BY PASSWORD '<hash>'`; liga-se como root através do `docker_process_sql` do entrypoint |

Notas:
- O `01-admin.sh` **não pode** ser executável (`chmod -x`), senão corre à parte e a função
  `docker_process_sql` não fica disponível.
- O hash não leva salt: usar passwords longas e `chmod 600 .env`.
- O hash só tem efeito no 1.º arranque. Depois:
  `ALTER USER 'admin'@'10.10.101.25' IDENTIFIED BY PASSWORD '*...';`

---

## 6. Como gerar o hash

**Pergunta:**
> como gero o hash para colocar lá

**Resposta:**

```bash
# Com Python (sem MariaDB)
read -rsp "Password: " P; echo
python3 -c "import hashlib,sys;print('*'+hashlib.sha1(hashlib.sha1(sys.argv[1].encode()).digest()).hexdigest().upper())" "$P"
unset P
```

```bash
# Numa MariaDB existente
docker exec -it <container_mariadb> mariadb -uroot -p -e "SELECT PASSWORD('aMinhaPassword');"
```

O resultado (ex.: `*94BDCEBE19083CE2A1F959FD02F964C7AF4CFC29`) vai para o `.env` com o `*`
incluído e sem aspas.

---

## 7. Script para gerar os hashes

**Pergunta:**
> podes gerar um script sh para fazer isso

**Resposta:**
Foi criado o `gerar-hash.sh`.

```bash
chmod +x gerar-hash.sh
./gerar-hash.sh            # usa o .env da mesma pasta
./gerar-hash.sh /caminho/.env
```

| Passo | Detalhe |
|---|---|
| Pergunta o user e o IP | Por omissão `admin` e `10.10.101.25` |
| Pede as passwords de root e admin | Sem eco, com confirmação, mínimo 12 caracteres |
| Calcula os hashes | `openssl`, ou `python3` se não houver openssl |
| Atualiza o `.env` | Substitui/acrescenta as `MARIADB_*` e mantém o resto |
| Segurança | Backup em `.env.bak` e `chmod 600` |

---

## 8. README

**Pergunta:**
> gerar o README.md a explicar tudo

**Resposta:**
Foi criado o `README.md`: estrutura, requisitos, instalação em 3 passos, teste de acesso,
configuração, limite de logs, operações comuns (password, IP, backup/restauro, reinicializar),
resolução de problemas e segurança.

---

## 9. Aviso de swarm mode

**Pergunta:**
> docker-compose up -d
> WARNING: The Docker Engine you're using is running in swarm mode.
> Compose does not use swarm mode to deploy services to multiple nodes in a swarm. All containers will be scheduled on the current node.
> To deploy your application across the swarm, use `docker stack deploy`.

**Resposta:**
É só um aviso: o contentor é criado neste nó. Confirmar com `docker ps --filter name=mariadb`.
Usar o comando v2: `docker compose up -d`.

| Opção | Quando faz sentido |
|---|---|
| Ignorar e continuar com compose | MariaDB num só servidor, dados em `./data` |
| `docker stack deploy` | Gerir pelo swarm ou pelo Portainer como serviço |

Para usar stack, o ficheiro muda em 4 pontos:

1. **Porta em `mode: host`**. Com o routing mesh, a MariaDB vê o IP do ingress e o
   `admin@10.10.101.25` deixa de funcionar.
2. **Fixar a um nó**, porque `./data` e `./conf` são locais.
3. **`deploy.restart_policy`** em vez de `restart`; o `container_name` é ignorado.
4. **Carregar o `.env` à mão**.

```yaml
services:
  mariadb:
    image: mariadb:11.4
    ports:
      - target: 3306
        published: 3306
        protocol: tcp
        mode: host
    deploy:
      placement:
        constraints: [node.hostname == <NOME_DO_NO>]
      restart_policy:
        condition: any
```

```bash
set -a; . ./.env; set +a
docker stack deploy -c docker-compose.yml mariadb
```
