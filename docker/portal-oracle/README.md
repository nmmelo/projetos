# Portal Oracle — gestão de espaço

Portal web em Python (Flask + Gunicorn + Nginx) para gerir bases de dados Oracle, com **utilizadores e perfis**. Para já faz o **shrink de tablespaces**, e está preparado para receber outras operações no futuro.

Corre num **contentor único em Ubuntu 26.04 LTS** com Nginx, Gunicorn, **OpenSSH** (para o VS Code Remote-SSH), **cron**, **git** e as ferramentas habituais (`ping`, `ip`, `vim`, `nano`).

---

## Funcionalidades

| Menu | Conteúdo |
|---|---|
| **Home** | Número de bases de dados visíveis, execuções em curso, execuções nas últimas 24 h, último backup, lista de bases de dados e últimas execuções |
| **Tablespaces** | Tamanho, usado e livre, do maior para o mais pequeno (sem SYSTEM, SYSAUX, UNDO e TEMP) |
| **Shrink** | Um tablespace ou TODOS, em ANÁLISE ou EXECUTAR, corre em segundo plano |
| **Execuções** | Histórico, passo em curso, log e cancelamento |
| **Configuração → Bases de dados** | Bases de dados Oracle: host, porta, SID ou Service Name, PDB, utilizador, password cifrada, Normal ou SYSDBA |
| **Configuração → Portal** | **Geral**, **Armazenamento**, **Utilizadores**, **Perfis** e **Backups** |
| **Minha área** (nome no canto superior direito) | Dados do utilizador e alteração da password |

### Situações que o shrink trata automaticamente

| Situação | O que o script faz |
|---|---|
| O `MOVE ONLINE` deixa o segmento novo **no fim do ficheiro**, porque o antigo e o novo coexistem durante o MOVE | **2.ª passagem**: se o HWM continuar acima do alvo, volta a mover esses segmentos, que vão então para o espaço libertado no início |
| Segmentos **`TEMPORARY`** órfãos, restos de MOVE/REBUILD que o SMON ainda não limpou | Lista-os. Em EXECUTAR, se não houver MOVE/REBUILD/CREATE INDEX a correr, executa o evento `DROP_SEGMENTS` (nível = `ts# + 1`) e espera até 60 s |
| `ORA-03297` no resize (há blocos usados acima do valor pedido) | Recalcula o HWM e tenta tamanhos progressivamente maiores (+10 % + 64 MB), até 6 tentativas |
| Objetos na recyclebin (`BIN$`) | Avisa, e com a opção PURGE faz `PURGE TABLESPACE` |

### Utilizadores e perfis

- O **administrador** é o único utilizador **local**: fica no ficheiro `data/admin.json`, tem todas as permissões e vê todas as bases de dados.
- Os restantes utilizadores ficam na **base de dados do portal** e cada um tem um **perfil**.
- Um perfil define:
  - **que bases de dados** o utilizador vê (todas, ou uma lista);
  - **o que pode fazer**:

| Permissão | Descrição |
|---|---|
| `tablespaces.ver` | Ver tablespaces |
| `shrink.analise` | Shrink em modo ANÁLISE |
| `shrink.executar` | Shrink em modo EXECUTAR e cancelar execuções |
| `config.bds` | Gerir as bases de dados Oracle |
| `config.portal` | Gerir utilizadores, perfis e backups |

A aba **Armazenamento** só está disponível para o administrador.

**Acrescentar uma funcionalidade no futuro:** registe a nova permissão em `ACTIONS`, no ficheiro `app/auth.py`, e proteja a rota com `@auth.requires("nova.permissao")`. A permissão aparece automaticamente no formulário dos perfis.

---

## Instalação com Docker

> Para instalar diretamente num servidor, sem Docker, veja [Instalação num servidor (sem Docker)](#instalação-num-servidor-sem-docker).

```bash
cd /opt/oracle-portal               # pasta com o código (fica no anfitrião)
cp .env.example .env
mkdir -p data/ssh/users data/home
cp ~/.ssh/id_ed25519.pub data/ssh/users/nmmelo.pub        # utilizador SSH pessoal (com sudo)

docker-compose build
docker-compose up -d
docker-compose exec -u portal portal python manage.py set-admin-password
```

| Acesso | Endereço |
|---|---|
| Portal | `http://<servidor>:8080` (utilizador `admin`) |
| SSH | `ssh -p 2222 nmmelo@<servidor>` |

> **A atualizar a partir da versão anterior?** O serviço passou a chamar-se `portal` (antes era `app`), e já não há um contentor separado para o Nginx. Corra `docker-compose down` antes do `up`. As bases de dados Oracle já configuradas são migradas automaticamente. Um `PORTAL_ADMIN_PASSWORD_HASH` que já esteja no `.env` é usado para criar o `admin.json` no primeiro arranque.

### Ficheiros em `data/` (não versionar — **fazer backup**)

| Ficheiro | Conteúdo |
|---|---|
| `admin.json` | Administrador local (hash da password) |
| `fernet.key` | Chave de cifra das passwords. **Sem ela, as passwords guardadas perdem-se** |
| `portal.json` | Onde ficam os dados do portal (SQLite, MariaDB ou PostgreSQL) |
| `portal.sqlite3` | Dados do portal, quando se usa SQLite |
| `backups/` | Ficheiros de backup da configuração |
| `ssh/` | Chaves do servidor SSH, `authorized_keys` do `portal` e `users/` (utilizadores pessoais) |
| `home/` | Homes dos utilizadores (`home/portal`, `home/nmmelo`, ...): `.vscode-server`, `.gitconfig`, histórico |

---

## Instalação num servidor (sem Docker)

Procedimento para **Ubuntu 26.04 LTS** (funciona também em 24.04/22.04 e Debian 12). O portal corre com o Gunicorn como serviço **systemd**, atrás do **Nginx**.

| Componente | Caminho / valor |
|---|---|
| Código | `/opt/oracle-portal` |
| Dados | `/opt/oracle-portal/data` (`admin.json`, `fernet.key`, SQLite, backups) |
| Ambiente Python | `/opt/oracle-portal/.venv` |
| Utilizador de serviço | `portal` (sem login) |
| Gunicorn | `127.0.0.1:8000`, só local |
| Nginx | porta 80/443, faz de proxy para o Gunicorn |

### 1. Pacotes

> **Ubuntu 26.04:** o `sudo` é o sudo-rs, compatível com os comandos abaixo. O SSH é o OpenSSH 10.2, que já não aceita chaves DSA nem assinaturas `ssh-rsa` com SHA-1: use chaves ed25519.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git curl
# opcional, para testar a BD do portal à mão:
sudo apt install -y mariadb-client      # se usar MariaDB
sudo apt install -y postgresql-client   # se usar PostgreSQL
```

| Pacote | Para quê |
|---|---|
| `python3`, `python3-venv`, `python3-pip` | Correr o portal num ambiente Python isolado (`.venv`). O Ubuntu 26.04 traz Python 3.14 e, por causa do PEP 668, não deixa instalar pacotes com `pip` fora de um venv |
| `nginx` | Servidor web e proxy para o Gunicorn |
| `git` | Obter e atualizar o código |

Não precisa do Oracle Instant Client: o `python-oracledb` em modo *thin* é Python puro. Os drivers de MariaDB (`pymysql`) e PostgreSQL (`psycopg[binary]`) também são instalados pelo `pip`, sem bibliotecas do sistema.

> **RHEL / Rocky / Alma:** `sudo dnf install -y python3.12 nginx git`. Os ficheiros do Nginx vão para `/etc/nginx/conf.d/` e o utilizador do Nginx é `nginx`. Com SELinux ativo, corra também `sudo setsebool -P httpd_can_network_connect 1`.

### 2. Utilizador e código

```bash
sudo useradd --system --home-dir /opt/oracle-portal --shell /usr/sbin/nologin portal
sudo mkdir -p /opt/oracle-portal
sudo unzip oracle-portal.zip -d /tmp && sudo cp -rT /tmp/oracle-portal /opt/oracle-portal
#   ou: sudo git clone <repositorio> /opt/oracle-portal
sudo mkdir -p /opt/oracle-portal/data/backups
sudo chown -R portal:portal /opt/oracle-portal

# o Nginx precisa de ler app/static; data/ fica so para o portal
sudo chmod 755 /opt/oracle-portal /opt/oracle-portal/app
sudo chmod -R o+rX /opt/oracle-portal/app/static
sudo chmod 700 /opt/oracle-portal/data
```

Para que outros administradores do servidor possam editar o código, acrescente-os ao grupo: `sudo usermod -aG portal nmmelo`.

### 3. Ambiente Python e pacotes

```bash
cd /opt/oracle-portal
sudo -u portal python3 -m venv .venv
sudo -u portal .venv/bin/pip install --upgrade pip
sudo -u portal .venv/bin/pip install -r requirements.txt
```

### 4. Configuração (`.env`)

```bash
sudo -u portal cp .env.example .env
sudo chmod 600 .env
sudo -u portal nano .env
```

Sem Docker, só estas variáveis contam (as `SSH_*` são ignoradas):

| Variável | Valor recomendado |
|---|---|
| `PORTAL_FERNET_KEY` | Vazio: a chave é criada em `data/fernet.key` |
| `PORTAL_COOKIE_SECURE` | `1` se usar HTTPS, `0` caso contrário |
| `TZ` | `Europe/Lisbon` |

### 5. Password do administrador

```bash
cd /opt/oracle-portal
sudo -u portal .venv/bin/python manage.py set-admin-password
#   Administrador: admin
#   Nova password: ********
#   Gravado em /opt/oracle-portal/data/admin.json
```

Para usar outro nome: `manage.py set-admin-password --user nuno`. O mesmo comando serve para repor a password se a esquecer.

### 6. Serviço systemd (Gunicorn)

```bash
sudo cp deploy/oracle-portal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oracle-portal
sudo systemctl status oracle-portal          # deve estar "active (running)"
curl -sI http://127.0.0.1:8000/login | head -1   # HTTP/1.1 200 OK
```

O ficheiro `deploy/oracle-portal.service` corre o Gunicorn como `portal`, lê o `.env` e reinicia o serviço se ele falhar.

### 7. Nginx

```bash
sudo cp deploy/nginx-oracle-portal.conf /etc/nginx/sites-available/oracle-portal
sudo ln -sf /etc/nginx/sites-available/oracle-portal /etc/nginx/sites-enabled/oracle-portal
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Se o servidor já tiver outros sites no Nginx, não apague o `default`. Em vez disso, mude o `server_name _;` para o nome DNS do portal (por exemplo `portal.dominio.pt`).

O portal fica em `http://<servidor>/` (utilizador `admin`).

### 8. HTTPS (recomendado)

**Com Let's Encrypt** (servidor acessível da Internet com nome DNS):
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d portal.dominio.pt
```

**Com certificado interno:** acrescente ao ficheiro do Nginx um bloco `listen 443 ssl;` com `ssl_certificate` e `ssl_certificate_key`.

Em ambos os casos, ponha `PORTAL_COOKIE_SECURE=1` no `.env` e corra `sudo systemctl restart oracle-portal`.

### 9. Firewall

```bash
sudo ufw allow 'Nginx Full'          # 80 e 443
sudo ufw allow OpenSSH
```

O servidor tem também de chegar às bases de dados Oracle (porta 1521, ou a do listener) e, se for o caso, à base de dados do portal (3306 MariaDB, 5432 PostgreSQL).

### 10. Backup automático (cron)

```bash
sudo crontab -u portal -e
```
```cron
0 2 * * * cd /opt/oracle-portal && .venv/bin/python manage.py backup --prune >> data/cron.log 2>&1
```

Se tiver definido `PORTAL_FERNET_KEY` no `.env`, o cron não a vê. Nesse caso, use antes:
`0 2 * * * cd /opt/oracle-portal && export $(grep -E '^PORTAL_FERNET_KEY=.' .env) && .venv/bin/python manage.py backup --prune >> data/cron.log 2>&1`

### Operação

| Objetivo | Comando |
|---|---|
| Estado / reiniciar | `sudo systemctl status oracle-portal` / `sudo systemctl restart oracle-portal` |
| Logs do portal | `sudo journalctl -u oracle-portal -f` |
| Logs do Nginx | `sudo tail -f /var/log/nginx/oracle-portal.*.log` |
| Repor a password do admin | `cd /opt/oracle-portal && sudo -u portal .venv/bin/python manage.py set-admin-password` |
| Backup manual | `cd /opt/oracle-portal && sudo -u portal .venv/bin/python manage.py backup --note "manual"` |

**Atualizar o portal:**
```bash
cd /opt/oracle-portal
sudo -u portal git pull                                   # ou copiar os ficheiros novos
sudo -u portal .venv/bin/pip install -r requirements.txt  # se o requirements.txt mudou
sudo systemctl restart oracle-portal
```

A pasta `data/` não é alterada por atualizações. Faça backup dela, sobretudo do `fernet.key` e do `admin.json`.

---

## Código no anfitrião (mount) e atualizações (Docker)

A imagem só contém o sistema: Ubuntu 26.04, Nginx, SSH, cron, git, Python 3.14 e os pacotes (num venv em `/opt/venv`). **Nada do portal é copiado para a imagem.** Tudo vem da pasta do anfitrião, montada em `/srv/portal`:

| Pasta / ficheiro no anfitrião | Conteúdo | Para aplicar uma alteração |
|---|---|---|
| `app/`, `wsgi.py`, `manage.py` | Código da aplicação | `supervisorctl restart gunicorn` |
| `docker/nginx.conf`, `docker/sshd.conf`, `docker/supervisord.conf` | Configuração do sistema (ligações simbólicas na imagem) | `docker-compose restart` |
| `docker/entrypoint.sh` | Preparação no arranque (utilizadores SSH, chaves, ...) | `docker-compose restart` |
| `requirements.txt` | Pacotes Python | `docker-compose restart` (o arranque instala as diferenças). Em alternativa, `docker-compose build` |
| `data/` | Dados, chaves, backups e homes | — |

Se o contentor (ou a imagem) for destruído, o código, as configurações e os dados ficam na pasta do anfitrião. Para voltar a pôr tudo a funcionar, basta `docker-compose up -d --build`.

**Atualizar o portal**, por exemplo com git:

```bash
cd /opt/oracle-portal && git pull          # no anfitrião, ou dentro do contentor por SSH
docker-compose restart                     # ou só: supervisorctl restart gunicorn
```

Só é preciso refazer a imagem (`docker-compose build`) para mudar o sistema base: pacotes apt ou versão do Ubuntu.

---

## Armazenamento: SQLite, MariaDB ou PostgreSQL

Escolhe-se em **Configuração → Portal → Armazenamento** (só o administrador):

1. Escolha o tipo e preencha host, porta, base de dados, utilizador e password (fica cifrada no `portal.json`).
2. Clique em **Testar ligação**. O portal liga e cria as tabelas.
3. Clique em **Guardar e mudar**, com a opção **Copiar os dados atuais** marcada.

A base de dados pode ser local (no anfitrião, ou noutro contentor) ou remota. Preparação:

```sql
-- MariaDB / MySQL
CREATE DATABASE portal CHARACTER SET utf8mb4;
CREATE USER 'portal'@'%' IDENTIFIED BY '...';
GRANT ALL ON portal.* TO 'portal'@'%';

-- PostgreSQL
CREATE USER portal PASSWORD '...';
CREATE DATABASE portal OWNER portal;
```

> Para ligar a uma base de dados no **anfitrião** a partir do contentor, use o IP da bridge do Docker (normalmente `172.17.0.1`). A base de dados tem de aceitar ligações nesse IP.

**Permissões no MariaDB.** O `GRANT` tem de ser para o host **de onde o contentor liga** (o MariaDB vê `172.17.0.1`, ou `172.18+.x.x` numa rede do docker-compose). Se o nome da base de dados tiver hífen, tem de ir entre backticks:

```sql
CREATE USER IF NOT EXISTS 'portal_oracle'@'172.%' IDENTIFIED BY '...';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP
   ON `portal-oracle`.* TO 'portal_oracle'@'172.%';
```

Sem isto, aparece `(1142, "SELECT command denied to user ...")`.

O administrador (`admin.json`), a chave de cifra e o `portal.json` ficam **sempre** em ficheiros locais.

---

## Backups da configuração

Em **Configuração → Portal → Backups**:

| Ação | Descrição |
|---|---|
| **Fazer backup agora** | Cria `data/backups/backup_<data>_manual_<id>.json` e regista-o, com uma nota opcional |
| **Importar de ficheiro** | Carrega um `.json`, que fica registado, e pode importá-lo logo |
| **Importar** (na lista) | Modo **juntar** (cria ou atualiza pelo nome) ou **substituir** (apaga as bases de dados, perfis e utilizadores atuais) |
| **Descarregar** | Faz download do ficheiro |
| **Remover** | Apaga o **ficheiro e o registo** |

- O backup inclui as definições, as bases de dados Oracle (passwords cifradas), os perfis e os utilizadores (hashes).
- Não inclui o histórico de execuções, o administrador local nem a configuração de armazenamento.
- Antes de qualquer importação, é criado um backup automático (origem `pre-import`).
- Se o backup tiver sido feito com outra `fernet.key`, o portal avisa: as passwords Oracle terão de ser introduzidas outra vez.

### Backup automático com o cron

Entre por SSH, abra o crontab com `crontab -e` e acrescente:

```cron
0 2 * * * . /etc/profile.d/portal.sh; cd /srv/portal && /opt/venv/bin/python manage.py backup --prune >> data/cron.log 2>&1
```

O `--prune` mantém só os N últimos backups do cron. N define-se em **Configuração → Portal → Geral**.

---

## SSH e VS Code

### Utilizadores pessoais (com sudo)

Cada pessoa tem o seu utilizador. Para o criar, ponha a chave pública num ficheiro com o nome do utilizador:

```bash
# no anfitrião, na pasta do portal
cp nmmelo_id_ed25519.pub   data/ssh/users/nmmelo.pub
cp adminssc_id_ed25519.pub data/ssh/users/adminssc.pub
docker-compose up -d --force-recreate
docker-compose logs portal | grep "Utilizador SSH"
#  Utilizador SSH: adminssc (uid 1001, sudo)
#  Utilizador SSH: nmmelo (uid 1002, sudo)
```

No arranque, o contentor faz isto para cada ficheiro `data/ssh/users/<utilizador>.pub`:

| O quê | Detalhe |
|---|---|
| Conta | Cria `<utilizador>` com home persistente em `data/home/<utilizador>` |
| Chaves | Copia o `.pub` para `~/.ssh/authorized_keys`. Pode ter várias chaves, uma por linha (por exemplo, PC e portátil) |
| sudo | `NOPASSWD`, em `/etc/sudoers.d/ssh-users`. As contas não têm password (só chave), por isso o sudo não a pode pedir. A imagem usa o `sudo` clássico, não o sudo-rs que o 26.04 traz por omissão |
| Grupos | `sshusers` (acesso SSH) e `portal` (editar o código e usar `supervisorctl`) |
| UID | Fixo, guardado em `data/ssh/users/<utilizador>.uid`, e não muda ao recriar o contentor |

**Remover um utilizador:** apague o `.pub` (e o `.uid`) e recrie o contentor. A home em `data/home/` fica guardada até a apagar.

Os nomes de utilizador só podem ter minúsculas, números, `_` e `-`. `root` e `portal` são reservados.

### Utilizador de serviço `portal`

É o utilizador que corre o Gunicorn. O acesso SSH com ele é opcional (chave em `data/ssh/authorized_keys` ou em `SSH_AUTHORIZED_KEYS`) e não tem sudo, a não ser que defina `PORTAL_SSH_SUDO=1`. Para trabalhar no dia a dia, use os utilizadores pessoais.

| Item | Valor |
|---|---|
| Porta | `2222` |
| Autenticação | Só chave pública. A password (`SSH_PASSWORD`) aplica-se apenas ao utilizador `portal` |
| Código | `/srv/portal` (montado a partir do anfitrião) |

**Editar o código com vários utilizadores.** Os ficheiros em `/srv/portal` pertencem ao dono no anfitrião. Para que o grupo `portal` (gid 1000) os possa editar, corra uma vez no anfitrião:

```bash
# NAO incluir data/ (homes e chaves SSH exigem permissoes restritas)
cd /opt/oracle-portal
sudo find . -path ./data -prune -o -exec chgrp 1000 {} + -exec chmod g+rwX {} +
sudo find . -path ./data -prune -o -type d -exec chmod g+s {} +
```

As sessões SSH usam `umask 002`, por isso os ficheiros novos ficam editáveis pelo grupo. Com sudo, também pode sempre usar `sudo`.

**`~/.ssh/config` no seu PC:**

```
Host oracle-portal
    HostName <servidor>
    Port 2222
    User nmmelo
    IdentityFile ~/.ssh/id_ed25519
    ForwardAgent yes        # usar a sua chave no git push/pull
```

**VS Code:** instale a extensão *Remote - SSH*, escolha **Connect to Host…** → `oracle-portal` e abra a pasta `/srv/portal`. O VS Code Server fica em `data/home`, por isso não volta a ser instalado quando o contentor é recriado.

**Git** (dentro do contentor):

```bash
git config --global user.name "Nuno"
git config --global user.email "..."
cd /srv/portal && git init && git remote add origin git@...:portal.git    # na 1.a vez
git pull / git push
```

**Aplicar alterações de código:**

```bash
supervisorctl restart gunicorn       # no terminal do VS Code / SSH (grupo portal)
```

Ou, a partir do anfitrião, `docker-compose restart`.

---

## Comandos úteis

| Objetivo | Comando (no anfitrião) |
|---|---|
| Logs (Nginx, Gunicorn, SSH, cron) | `docker-compose logs -f` |
| Estado dos processos | `docker-compose exec portal supervisorctl status` |
| Shell como root | `docker-compose exec portal bash` |
| Alterar a password do admin | `docker-compose exec -u portal portal python manage.py set-admin-password` |
| Backup manual pela linha de comandos | `docker-compose exec -u portal portal python manage.py backup --note "antes de atualizar"` |

---

## Bases de dados Oracle: privilégios

| Ligação | Necessário | Notas |
|---|---|---|
| SYSDBA | Password file (`remote_login_passwordfile = EXCLUSIVE`) | Usa `X$KTFBUE`: o cálculo do HWM é muito mais rápido |
| Normal | `SELECT ANY DICTIONARY`, `ALTER DATABASE`, `ALTER ANY TABLE`, `ALTER ANY INDEX`, `ALTER SYSTEM` (para cancelar) | Usa `DBA_EXTENTS`, que é mais lento |

O driver é o **python-oracledb** em modo *thin* (sem Instant Client). Requer Oracle 12.1 ou superior e não suporta Native Network Encryption: nesses casos, use o modo *thick*.

---

## Problemas comuns

| Erro | Causa e solução |
|---|---|
| Login falha logo depois de instalar | Corra `manage.py set-admin-password` (comando na tabela "Comandos úteis") |
| `No supported authentication methods` / `Permission denied (publickey)` com uma chave que funciona noutros servidores | Veja o motivo em `docker-compose logs portal \| grep sshd`. `bad ownership or modes`: recrie o contentor, que corrige as permissões. `ssh-rsa not in PubkeyAcceptedAlgorithms`: atualize o PuTTY (0.75 ou superior) ou use uma chave ed25519 |
| `Permission denied (publickey)` no SSH | A chave não está em `data/ssh/users/<utilizador>.pub`. Depois de a acrescentar, recrie o contentor e confirme com `docker-compose logs portal \| grep "Utilizador SSH"` |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED` | Apagou `data/ssh/`. Remova a linha antiga do `~/.ssh/known_hosts` |
| `Driver Python em falta` no Armazenamento | Recrie a imagem: `docker-compose build --no-cache` |
| `dubious ownership` no git | Já está tratado para `/srv/portal`. Noutras pastas, use `git config --global --add safe.directory <pasta>` |
| Execução em `INTERROMPIDO` | O portal foi reiniciado durante a execução. Confirme em `V$SESSION` se a sessão Oracle continuou |
