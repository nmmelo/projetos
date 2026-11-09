# dev-portal — Contentor Ansible + Portal Python (Ubuntu 26.04)

Contentor Ubuntu 26.04 com Ansible, SSH e um portal web em Python, servido por **Nginx** (reverse proxy) e **Gunicorn** (servidor da aplicação).

```
Browser ──► :8080 ──► Nginx (:80) ──► Gunicorn (127.0.0.1:8000) ──► app.py (Flask)
                        └── /static servido diretamente pelo Nginx
SSH     ──► :2222 ──► sshd (:22)
```

---

## 1. O que está instalado

| Categoria | Pacotes |
|---|---|
| Base | `ansible`, `git`, `sudo`, `curl`, `nano`, `tzdata`, `ca-certificates` |
| SSH | `openssh-server`, `openssh-client` |
| Rede | `iputils-ping` (`ping`), `iproute2` (`ip`), `dnsutils` (`dig`), `net-tools` (`netstat`) |
| Web | `nginx` |
| Python | `python3`, `python3-pip`, `python3-venv`, `python3-dev` |
| MariaDB | `libmariadb-dev`, `pkg-config`, `build-essential`, `mariadb-client` |
| venv `/opt/portal/venv` | `flask`, `gunicorn`, `mysqlclient`, `pymysql`, `sqlalchemy`, `python-dotenv` |

**Utilizadores:** `nmmelo` e `adminssc`, ambos com `sudo` sem password. O login SSH é feito só por chave pública e o `root` não pode entrar por SSH.

---

## 2. Estrutura de ficheiros

```
.
├── README.md
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── portal-nginx.conf    # Site do Nginx (reverse proxy)
├── portal/              # Código do portal  (montado em /opt/portal/app)
│   ├── app.py           # App Flask de exemplo
│   └── static/          # opcional: CSS, JS, imagens
├── workspace/           # montado em /home/nmmelo/workspace
└── secrets/
    ├── ansible.cfg
    ├── .vault           # password do Ansible Vault
    ├── chave.pub       # chave pública de login

```

| Ficheiro | Função |
|---|---|
| `Dockerfile` | Constrói a imagem: pacotes, utilizadores, SSH, venv Python e o site do Nginx |
| `docker-compose.yml` | Define o serviço `ansible`, as portas, os volumes e os secrets |
| `entrypoint.sh` | No arranque instala as chaves SSH e mantém as host keys entre recriações. Valida o `app.py` e depois arranca o Gunicorn (com `--reload`), o Nginx e o `sshd` |
| `portal-nginx.conf` | Envia os pedidos para o Gunicorn em `127.0.0.1:8000` e serve `/static` diretamente |
| `portal/app.py` | Aplicação Flask mínima, para testar |
| `.gitignore` | Impede que `secrets/`, `workspace/` e `portal/.env` vão para o Git |

---

## 3. Primeira instalação

### 3.1 Obter os ficheiros
```bash
git clone https://github.com/nmmelo/projetos.git
cp -r projetos/docker/dev-portal /volume1/docker/dev-portal
cd /volume1/docker/dev-portal
sed -i 's/\r$//' Dockerfile entrypoint.sh portal-nginx.conf docker-compose.yml
```
As pastas `portal/`, `workspace/` e `secrets/` já vêm criadas. O `portal/app.py` é uma app de exemplo.

### 3.2 Preparar os secrets
```bash
# Chave pública com que vais entrar por SSH
cp ~/.ssh/id_ed25519.pub secrets/nmmelo.pub

# Chave do adminssc (gerar, se ainda não existir)
ssh-keygen -t ed25519 -N '' -f secrets/adminssc

# Password do Ansible Vault e configuração do Ansible
echo 'A_TUA_PASSWORD' > secrets/.vault
touch secrets/ansible.cfg

chmod 600 secrets/adminssc secrets/.vault
```

### 3.3 Construir e arrancar
```bash
docker compose up -d --build
```

### 3.4 Verificar
| Verificação | Comando | Esperado |
|---|---|---|
| Estado | `docker compose ps` | `ansible` com o estado `running` |
| Logs de arranque | `docker compose logs -f ansible` | Sem erros |
| Portal | `curl http://localhost:8080` | `Portal Python a funcionar.` |
| SSH | `ssh -p 2222 nmmelo@localhost` | Login com chave |
| Rede | `docker compose exec ansible ping -c2 8.8.8.8` | Respostas |
| Interfaces | `docker compose exec ansible ip a` | Lista de interfaces |
| Processos | `docker compose exec ansible ps aux \| grep -E 'gunicorn\|nginx'` | Ambos em execução |

---

## 4. Desenvolver o portal

> O contentor chama-se `dev-portal` e o serviço Compose chama-se `ansible`. Os comandos `docker compose ...` usam `ansible`; os comandos `docker ...` usam `dev-portal` (ex.: `docker exec -it dev-portal bash`). No Synology com Compose v1, usa `docker-compose` (com hífen).

O código fica em **`./portal`**, no anfitrião. Tudo o que alterares aqui aparece logo dentro do contentor, em `/opt/portal/app`.

| Ação | O que fazer |
|---|---|
| Editar o código | Edita os ficheiros em `./portal` |
| Aplicar alterações | Automático: o Gunicorn corre com `--reload` e recarrega ao guardar ficheiros `.py` |
| Ficheiros estáticos | Põe-nos em `./portal/static/`. Ficam acessíveis em `http://localhost:8080/static/...` |
| Instalar pacotes Python | `docker compose exec ansible /opt/portal/venv/bin/pip install <pacote>` (ver nota abaixo) |

**Nota:** os pacotes instalados com `pip install` dentro do contentor perdem-se quando o contentor é recriado. Para os manteres, acrescenta-os à linha `pip install` do `Dockerfile` e reconstrói a imagem com `docker compose up -d --build`.

### Templates HTML
O `--reload` só vigia ficheiros `.py`. Para recarregar os templates do Flask, põe no `app.py`:
```python
app.config["TEMPLATES_AUTO_RELOAD"] = True
```

### Outros frameworks
O Gunicorn arranca com `app:app`, ou seja, o ficheiro `app.py` e o objeto `app`. Para outros frameworks, altera essa parte no `entrypoint.sh`:

| Framework | Alteração no `entrypoint.sh` | Pacote extra |
|---|---|---|
| Flask | `app:app` (atual) | — |
| FastAPI | `-k uvicorn.workers.UvicornWorker app:app` | `uvicorn`, `fastapi` |
| Django | `projeto.wsgi:application` | `django` |

### Ligação ao MariaDB (exemplo)
```python
import pymysql
conn = pymysql.connect(host="IP_DO_MARIADB", user="portal",
                       password="...", database="portal")
```
Não ponhas passwords no código. Guarda-as num ficheiro `./portal/.env` e lê-as com o `python-dotenv`.

---

## 5. Persistência de dados

| Dados | Onde ficam | `docker compose down` | `docker compose down -v` |
|---|---|---|---|
| Código do portal | `./portal` (anfitrião) | ✅ Mantém | ✅ Mantém |
| Workspace | `./workspace` (anfitrião) | ✅ Mantém | ✅ Mantém |
| Secrets | `./secrets` (anfitrião) | ✅ Mantém | ✅ Mantém |
| Host keys SSH | volume `ssh_hostkeys` | ✅ Mantém | ❌ Apaga |
| Logs do Nginx | volume `nginx_logs` | ✅ Mantém | ❌ Apaga |
| Logs do Gunicorn, pacotes `pip` instalados à mão | dentro do contentor | ❌ Apaga | ❌ Apaga |

Se o volume `ssh_hostkeys` for apagado, o servidor SSH gera chaves novas. O cliente SSH vai mostrar o aviso *host key changed*, que se resolve com:
```bash
ssh-keygen -R '[localhost]:2222'
```

---

## 6. Comandos úteis

| Ação | Comando |
|---|---|
| Arrancar | `docker compose up -d` |
| Parar | `docker compose stop` |
| Reiniciar o contentor | `docker compose restart ansible` |
| Recarregar só o Gunicorn | `docker compose exec ansible sh -c 'kill -HUP $(cat /run/gunicorn.pid)'` |
| Recarregar só o Nginx | `docker compose exec ansible nginx -s reload` |
| Reconstruir depois de alterar o Dockerfile | `docker compose up -d --build` |
| Reconstruir sem cache | `docker compose build --no-cache && docker compose up -d` |
| Remover o contentor | `docker compose down` |
| Abrir uma shell no contentor | `docker compose exec ansible bash` |
| Logs do contentor | `docker compose logs -f ansible` |
| Logs do Nginx | `docker compose exec ansible tail -f /var/log/nginx/portal_error.log` |
| Logs do Gunicorn | `docker compose exec ansible tail -f /var/log/gunicorn/error.log` |

---

## 7. Resolução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| O contentor reinicia sem parar | Erro no arranque (ver `docker logs dev-portal`), por exemplo uma opção do Gunicorn sem valor | Corrigir o `entrypoint.sh` e correr `docker compose up -d --build` |
| `nginx: [emerg] ...` nos logs | Erro no `portal-nginx.conf` | Corrigir a linha indicada |
| O contentor reinicia sem parar, com `ERRO: /opt/portal/app/app.py não existe` | A pasta `./portal` está vazia | Pôr o `app.py` em `./portal/` |
| `curl` devolve **502 Bad Gateway** | O Gunicorn não está a correr, normalmente por um erro no `app.py` | Ver `/var/log/gunicorn/error.log` |
| `curl` devolve **404** em `/static/...` | O ficheiro não está em `./portal/static/` | Confirmar o caminho |
| `Permission denied (publickey)` no SSH | A chave em `secrets/nmmelo.pub` está errada | Corrigir a chave e correr `docker compose restart ansible` |
| `ssh-keygen -l` falha no arranque | O ficheiro `nmmelo.pub` não é uma chave válida | Gerar ou copiar a chave correta |
| A porta 8080 ou 2222 já está ocupada | Outro serviço está a usá-la | Mudar a porta em `ports:` no `docker-compose.yml` |
| `mysqlclient` falha ao compilar | Faltam bibliotecas de desenvolvimento | Já estão incluídas no Dockerfile. Reconstruir com `--no-cache` |

---

## 8. Notas de segurança

- O `.gitignore` já exclui `secrets/`, `workspace/` e `portal/.env`. Não os acrescentes ao Git à mão.
- O portal usa HTTP simples. Para HTTPS, usa o Reverse Proxy do DSM (Synology) à frente da porta 8080.
- O `--reload` do Gunicorn é para desenvolvimento. Em produção, retira-o do `entrypoint.sh`.
- Os dois utilizadores têm `sudo` sem password. Limita esse acesso se o contentor ficar exposto à rede.
