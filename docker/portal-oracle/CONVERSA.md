# Conversa: portal Oracle em Python (Docker)

Chat com o Claude, de 5 a 6 de novembro de 2026. As perguntas estão como foram escritas; as respostas
estão resumidas, mas mantêm os comandos e as conclusões. Os ficheiros finais estão nesta pasta.
O script de shrink que o portal executa está em [`oracledb/query/shrink_tablespace`](../../oracledb/query/shrink_tablespace).

---

## 1. Portal em Python para o shrink

**Pergunta:**
> é possível colocar neste script e colocar num portal desenvolvido em python , que permita escolher a base de dados, tendo para isso uma zona de configuração de base de dados, onde colocamos o host, porta, SID, ServiceName, PDB, user, password (encriptada) e se é user normal ou sysdba, devendo usar para isso algum driver em python que permita fazer a ligação da base de dados

**Resposta:**

| Componente | Escolha |
|---|---|
| Web | Flask + Gunicorn + Nginx |
| Driver Oracle | `python-oracledb` em modo *thin* (sem Oracle Instant Client) |
| Passwords | Cifradas com Fernet (`data/fernet.key`) |
| Execução | Em segundo plano; passo atual lido de `V$SESSION.ACTION`; log e cancelamento (`KILL SESSION`) |

---

## 2. Primeiros problemas com o docker-compose v1

| Erro | Causa e solução |
|---|---|
| `yaml.parser.ParserError` | Indentação: `volumes:` com 2 espaços em vez de 4 |
| `Utilizador ou password invalidos` | O `docker-compose` **v1** não converte `$$` em `$` no `.env`. O `manage.py hash-password` passou a mostrar as duas versões, e o portal aceita ambas |

---

## 3. Utilizadores, perfis, backups e contentor único

**Pergunta (resumo):**
> permitir criar utilizadores, perfil ... escolher que base de dados podemos ver e o que fazer ... apenas um container com o pyhton e o nginx, tambem deve ter o openssh ... vscode ... mariadb ou postgres ... git ... ping, ip, vim, nano, cron ... minha area ... O admin é o único utilizador local, ficheiro ... backup ... Home page, onde temos o numero de base de dados.

**Resposta:**

| Pedido | Implementação |
|---|---|
| Home | Nº de bases de dados, execuções em curso e nas últimas 24 h, último backup |
| Perfis | Bases de dados visíveis + permissões (`tablespaces.ver`, `shrink.analise`, `shrink.executar`, `config.bds`, `config.portal`). Para novas funcionalidades basta registar a ação em `app/auth.py` |
| Admin | Único utilizador local, em `data/admin.json` (`manage.py set-admin-password`) |
| Configuração | Abas **Bases de dados** e **Portal** (Geral, Armazenamento, Utilizadores, Perfis, Backups) |
| Armazenamento | SQLite, MariaDB ou PostgreSQL, local ou remoto, com cópia dos dados |
| Backups | Criar, importar (juntar/substituir), descarregar, remover ficheiro e registo; `manage.py backup --prune` no cron |
| Contentor | Nginx + Gunicorn + OpenSSH + cron + git, geridos pelo supervisord |

---

## 4. SSH com utilizadores pessoais

**Pergunta:**
> quero incluir as chaves publica de dois utilizadores, o nmmelo e o adminssc no container, com sudoers.

**Resposta:** um ficheiro `data/ssh/users/<utilizador>.pub` por pessoa. No arranque, o contentor cria a conta
(UID fixo em `.uid`), dá-lhe sudo `NOPASSWD` e adiciona-a aos grupos `sshusers` e `portal`.

Problema encontrado no NAS Synology (`/volume1`): `Could not open user 'nmmelo' authorized keys ...: Permission denied`.
O `sshd` lê as chaves com a identidade do utilizador e a ACL da partilha impedia a leitura. As chaves passaram
para `/etc/ssh/authorized_keys/<utilizador>`, dentro do contentor (`AuthorizedKeysFile`). Também foram
corrigidos os fins de linha Windows (CRLF) e as permissões das homes.

---

## 5. Tudo no mount do anfitrião

**Pergunta:**
> quero que app seja um mount e não seja copiado para a vm, porque a ideia é puder atualizar, se o container for destruído, pelo menos essa informação é guardada.

**Resposta:** a imagem só tem o sistema. O código (`app/`), as configurações (`docker/*.conf` e
`entrypoint.sh`, através de ligações simbólicas), os dados e as homes vêm todos da pasta do anfitrião.
Alterações ao `requirements.txt` são instaladas no arranque. O `.dockerignore` evita enviar `data/` para o build.

```yaml
volumes:
  - ./:/srv/portal          # código + data/
  - ./data/home:/home       # homes dos utilizadores
```

---

## 6. Ubuntu 26.04 e instalação sem Docker

**Perguntas:**
> e debian a distribuição, eu quero ubuntu 26.04
> pode ter no readme.md, o procedimento para instalar e configurar num servidor e não num container ...

**Resposta:**

| No Ubuntu 26.04 | Adaptação |
|---|---|
| Utilizador `ubuntu` com o UID 1000 na imagem | Removido |
| Python 3.14 + PEP 668 | venv em `/opt/venv` |
| sudo-rs por omissão | Instalado o `sudo` clássico, para o `sudoers.d` |

O README passou a ter a instalação num servidor: pacotes, utilizador `portal`, venv, `.env`, password do
admin, serviço systemd (`deploy/oracle-portal.service`), Nginx (`deploy/nginx-oracle-portal.conf`), HTTPS,
firewall e cron.

---

## 7. MariaDB como armazenamento

**Erro:**
> Erro: (1142, "SELECT command denied to user 'portal_oracle'@'172.17.0.1' for table `portal-oracle`.`settings`")

**Resposta:** a ligação funcionava, mas faltava o `GRANT` para o host de onde o contentor liga, com o nome da
base de dados entre backticks por causa do hífen:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP
   ON `portal-oracle`.* TO 'portal_oracle'@'172.%';
```
