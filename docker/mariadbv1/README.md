# MariaDB em Docker

Instância MariaDB 11.4 em Docker Compose, na porta por omissão (3306), com:

- um utilizador **admin** com permissões de DBA, acessível **apenas a partir de `10.10.101.25`**;
- o utilizador **root** restrito ao interior do contentor;
- passwords guardadas no `.env` **apenas como hash** (nunca em claro);
- dados e configurações em pastas montadas (`data` e `conf`).

---

## Estrutura

```
mariadb/
├── docker-compose.yml    # Definição do serviço
├── .env                  # Variáveis (hashes das passwords, user, IP)
├── gerar-hash.sh         # Gera os hashes e atualiza o .env
├── conf/
│   └── custom.cnf        # Configuração da MariaDB
├── init/
│   └── 01-admin.sh       # Cria o user admin (só no 1.º arranque)
└── data/                 # Dados da base de dados (criada automaticamente)
```

| Pasta no host | Pasta no contentor | Modo | Conteúdo |
|---|---|---|---|
| `./data` | `/var/lib/mysql` | leitura/escrita | Ficheiros da base de dados |
| `./conf` | `/etc/mysql/conf.d` | só leitura | Ficheiros `.cnf` de configuração |
| `./init` | `/docker-entrypoint-initdb.d` | só leitura | Scripts de inicialização |

---

## Requisitos

- Docker e Docker Compose (v2 — comando `docker compose`)
- `openssl` ou `python3` (para o `gerar-hash.sh`)
- Porta 3306 livre no host

---

## Instalação

### 1. Gerar os hashes das passwords

```bash
chmod +x gerar-hash.sh
./gerar-hash.sh
```

O script pede:

| Pergunta | Valor por omissão |
|---|---|
| Utilizador admin | `admin` |
| IP de acesso do admin | `10.10.101.25` |
| Password do root (2×) | — |
| Password do admin (2×) | — |

As passwords não aparecem no ecrã, são confirmadas e têm de ter **pelo menos 12 caracteres**.
O `.env` fica atualizado, com permissões `600`, e a versão anterior é guardada em `.env.bak`.

Resultado no `.env`:

```
MARIADB_ROOT_PASSWORD_HASH=*225345A0C8CAAFD63C8D80085ACAC8ADA9C5F54B
MARIADB_ADMIN_USER=admin
MARIADB_ADMIN_PASSWORD_HASH=*BEF2D191C917590A623399610FFD094C35A8EF24
MARIADB_ADMIN_HOST=10.10.101.25
```

> Apague o `.env.bak` se não precisar dele: `rm .env.bak`

### 2. Confirmar as permissões do script de init

O `init/01-admin.sh` **não pode ser executável**, para que o entrypoint da imagem o carregue
(em vez de o executar) e a função interna `docker_process_sql` fique disponível.

```bash
chmod -x init/01-admin.sh
```

### 3. Validar e arrancar

```bash
docker compose config -q      # valida a sintaxe
docker compose up -d
docker compose logs -f        # acompanhar o arranque
```

O contentor fica `healthy` quando a base de dados está pronta:

```bash
docker compose ps
```

---

## Testar o acesso

A partir da máquina `10.10.101.25`:

```bash
mariadb -h <IP_DO_SERVIDOR> -P 3306 -u admin -p
```

Confirmar permissões:

```sql
SHOW GRANTS FOR CURRENT_USER;
-- GRANT ALL PRIVILEGES ON *.* TO `admin`@`10.10.101.25` ... WITH GRANT OPTION
```

Acesso como root (só de dentro do contentor):

```bash
docker exec -it mariadb mariadb -uroot -p
```

---

## Gerar um hash manualmente

Se precisar de um hash fora do script:

```bash
# Sem MariaDB
read -rsp "Password: " P; echo
python3 -c "import hashlib,sys;print('*'+hashlib.sha1(hashlib.sha1(sys.argv[1].encode()).digest()).hexdigest().upper())" "$P"
unset P
```

```sql
-- Numa MariaDB existente
SELECT PASSWORD('aMinhaPassword');
```

O hash tem o formato `*` + 40 caracteres hexadecimais e coloca-se no `.env` **sem aspas**.

---

## Configuração (`conf/custom.cnf`)

| Parâmetro | Valor | Descrição |
|---|---|---|
| `bind-address` | `0.0.0.0` | Aceita ligações de qualquer interface |
| `port` | `3306` | Porta por omissão |
| `character-set-server` | `utf8mb4` | Charset completo (inclui emojis) |
| `collation-server` | `utf8mb4_unicode_ci` | Collation |
| `max_connections` | `200` | Ligações simultâneas |
| `skip-name-resolve` | `ON` | Sem DNS reverso — obrigatório para o controlo por IP funcionar de forma fiável |
| `innodb_buffer_pool_size` | `512M` | Ajustar a ~50–70% da RAM dedicada |

Depois de alterar a configuração:

```bash
docker compose restart
```

---

## Limitar o tamanho dos logs do Docker

Para evitar que o `*-json.log` do contentor encha o disco, acrescentar ao serviço no
`docker-compose.yml` (ao mesmo nível de `image`):

```yaml
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
```

Aplicar com:

```bash
docker compose up -d --force-recreate
```

---

## Operações comuns

### Alterar a password de um utilizador existente

O `.env` e o `init/` **só têm efeito no 1.º arranque** (com `data/` vazia). Depois disso:

```bash
./gerar-hash.sh   # ou gerar o hash manualmente
docker exec -it mariadb mariadb -uroot -p
```

```sql
ALTER USER 'admin'@'10.10.101.25' IDENTIFIED BY PASSWORD '*NOVO_HASH';
```

### Alterar o IP de acesso do admin

```sql
RENAME USER 'admin'@'10.10.101.25' TO 'admin'@'NOVO_IP';
```

Atualizar também o `MARIADB_ADMIN_HOST` no `.env`, para manter coerência.

### Backup e restauro

```bash
read -rsp "Password root: " P; echo

# Backup
docker exec mariadb mariadb-dump -uroot -p"$P" --all-databases --single-transaction > backup_$(date +%F).sql

# Restauro
docker exec -i mariadb mariadb -uroot -p"$P" < backup_AAAA-MM-DD.sql

unset P
```

> Com redirecionamento (`>` / `<`) não há terminal para pedir a password, por isso é passada
> pela variável `P`, lida sem eco e apagada no fim.

### Reinicializar do zero

> ⚠️ **Apaga todos os dados.**

```bash
docker compose down
rm -rf data/*
docker compose up -d
```

---

## Resolução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Access denied for user 'admin'@'X.X.X.X'` | Ligação de um IP diferente de `10.10.101.25` | Ligar a partir do IP autorizado ou alterar o host do user |
| User admin não existe | `data/` já tinha dados quando o init devia correr | Criar o user à mão ou reinicializar do zero |
| `docker_process_sql: command not found` nos logs | `init/01-admin.sh` está executável | `chmod -x init/01-admin.sh` e reinicializar |
| `Can't connect to MariaDB server` | Firewall ou porta 3306 ocupada | `ss -tlnp \| grep 3306`; verificar a firewall |
| `services.mariadb.logging.options must be a mapping` | Sintaxe YAML errada em `options` | Usar `chave: "valor"` com espaço, sem `-` nem `=` |
| Contentor não fica `healthy` | Erro de configuração | `docker compose logs mariadb` |

---

## Segurança

- O hash `mysql_native_password` não leva salt: o mesmo texto gera sempre o mesmo hash. Use
  passwords **longas e únicas**.
- Mantenha o `.env` com `chmod 600` e **fora de repositórios Git** (adicionar ao `.gitignore`).
- O root só aceita ligações a partir de `localhost` (interior do contentor).
- O Docker publica portas contornando o `ufw`/`firewalld`. O controlo de acesso por IP é feito
  pelo próprio MariaDB (`admin@10.10.101.25`). Para filtrar também ao nível da rede, use a chain
  `DOCKER-USER` do iptables.
