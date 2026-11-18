# ansible-semaphore

Dois contentores para correr playbooks Ansible num Synology (ou noutro anfitrião Docker), pensados para uma stack do
**Portainer** a partir do Git:

| Serviço | Imagem | O que faz |
|---|---|---|
| `ansible` | Ubuntu 26.04 (build local) | Ansible + Git + SSH. Utilizador principal com sudo (só chave pública), utilizador extra opcional, chave privada para os servidores de destino, `ansible.cfg` e password do vault |
| `semaphore` | `semaphoreui/semaphore` | Interface web do Ansible (SQLite). Vê os mesmos playbooks em `/playbooks` (só leitura) |

Nada sensível fica no Git: chaves, `ansible.cfg`, password do vault e `.env` ficam no anfitrião, em `DATA_DIR/secrets`, ou
nas variáveis da stack do Portainer.

## Arquitetura

```
ssh  ──► :2222 ─► ansible   (sshd) ─► ~/workspace  ◄── PLAYBOOKS_DIR ──┐
                     │                                                 │
                     └─► ssh <utilizador de destino>@servidores        │
web  ──► :3000 ─► semaphore ─► /playbooks (só leitura) ◄───────────────┘
```

## Ficheiros

```
ansible-semaphore/
├── Dockerfile                    # Ubuntu 26.04 + ansible + git + openssh + sudo
├── docker-compose.yml            # ansible + semaphore, tudo por variáveis
├── entrypoint.sh                 # host keys, utilizadores, chave privada, ansible.cfg, vault, sshd
├── ssh-utilizadores.sh           # utilizador principal + extra, chaves públicas e sudo em cada arranque
├── preparar.sh                   # cria as pastas dos mounts (obrigatório no Synology)
├── .env.example                  # → .env (ou variáveis no Portainer)
└── secrets/
    ├── README.md                 # o que vai nesta pasta
    └── ansible.cfg.example       # → secrets/ansible.cfg
```

## Mounts

Pastas no anfitrião (nenhuma vai para o Git):

| Anfitrião | Contentor | Conteúdo |
|---|---|---|
| `PLAYBOOKS_DIR` | `ansible:/home/<SSH_USER>/workspace` | Playbooks, inventário, roles (ex. clone do repositório Git dos playbooks) |
| `PLAYBOOKS_DIR` | `semaphore:/playbooks` (só leitura) | Os mesmos playbooks para o Semaphore |
| `DATA_DIR/secrets` | `ansible:/secrets` (só leitura) | `ansible.cfg`, `.vault_pass`, chave privada, `authorized_keys` |
| `DATA_DIR/hostkeys` | `ansible:/etc/ssh/keys` | Host keys SSH (a impressão digital não muda ao recriar) |
| `DATA_DIR/semaphore/data` | `semaphore:/var/lib/semaphore` | Base de dados SQLite |
| `DATA_DIR/semaphore/config` | `semaphore:/etc/semaphore` | Configuração |
| `DATA_DIR/semaphore/tmp` | `semaphore:/tmp/semaphore` | Clones dos repositórios das tarefas |

## Variáveis

| Variável | Omissão | Descrição |
|---|---|---|
| `DATA_DIR` | `.` | Pasta com `secrets/`, `hostkeys/` e `semaphore/`. **Caminho absoluto no Portainer** |
| `PLAYBOOKS_DIR` | `./workspace` | Pasta dos playbooks. **Caminho absoluto no Portainer** |
| `SSH_USER` | `admin` | Utilizador principal (sudo, só chave) — build |
| `SSH_UID` / `SSH_GID` | `1026` / `100` | Iguais aos do anfitrião (`id <utilizador>`) — build |
| `SSH_PUBKEY` | | Chave(s) pública(s) do principal, além de `secrets/authorized_keys`; várias separadas por `;` |
| `SSH_EXTRA_USER` | | Utilizador extra (ex. o de automação nos servidores de destino) |
| `SSH_EXTRA_PUBKEY` | | Chave(s) pública(s) do extra; vazio = não entra por SSH |
| `SSH_EXTRA_SUDO` / `SSH_EXTRA_UID` | `1` / | Sudo sem password do extra; UID opcional |
| `ANSIBLE_KEY_FILE` | | Nome da chave privada em `secrets/`; instalada em `~/.ssh/<nome>` do principal e do extra |
| `SEMAPHORE_VERSION` | `v2.18.31` | Tag da imagem do Semaphore |
| `SEMAPHORE_ADMIN` / `_NAME` | `admin` / `Administrador` | Administrador (só no 1.º arranque) |
| `SEMAPHORE_ADMIN_EMAIL` / `_PASSWORD` | — | Obrigatórios |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | — | Obrigatório: `openssl rand -base64 32`. Guardar; não mudar depois |
| `SSH_PORT` / `SEMAPHORE_PORT` | `2222` / `3000` | Portas no anfitrião |
| `ANSIBLE_CONTAINER` / `SEMAPHORE_CONTAINER` | `ansible` / `semaphore` | Nomes dos contentores |
| `TZ` | `Europe/Lisbon` | Fuso horário |

## Instalação no Portainer (stack a partir do Git)

**1. Apagar a instalação antiga** (se existir): Stacks → stack antiga → **Delete this stack**; em **Volumes**, apagar os
volumes antigos (`..._ssh_hostkeys`, `..._semaphore_data`, `..._semaphore_config`, `..._semaphore_tmp`); em **Images**,
apagar a imagem local antiga.

**2. Preparar as pastas no anfitrião** (SSH no Synology, uma vez):

```bash
mkdir -p /volume1/docker/ansible-semaphore && cd /volume1/docker/ansible-semaphore
# copiar para aqui o preparar.sh e a pasta secrets/ do repositório (ou fazer git clone e usar esta pasta)
DATA_DIR=/volume1/docker/ansible-semaphore \
PLAYBOOKS_DIR=/volume1/docker/ansible-semaphore/workspace \
SSH_UID=1026 SSH_GID=100 ./preparar.sh
```

Depois, em `/volume1/docker/ansible-semaphore/secrets/`:

| Ficheiro | Ação |
|---|---|
| `ansible.cfg` | Rever `remote_user` e `private_key_file` (o `preparar.sh` cria-o a partir do exemplo) |
| `<ANSIBLE_KEY_FILE>` | Copiar a chave privada (`chmod 600`) |
| `.vault_pass` | Copiar a password do vault (`chmod 600`), se usar vault |

E os playbooks em `workspace/` (ex. `git clone <repositório-dos-playbooks> workspace/ansible`).

**3. Criar a stack**: Stacks → **Add stack** → **Repository**

| Campo | Valor |
|---|---|
| Repository URL | URL deste repositório |
| Repository reference | `refs/heads/main` |
| Compose path | `docker/ansible-semaphore/docker-compose.yml` |
| Environment variables | As do `.env.example`, com `DATA_DIR` e `PLAYBOOKS_DIR` em caminho absoluto |

**Deploy the stack.** A imagem `ansible` é construída no deploy (`pull_policy: build`). Para reconstruir depois de mudar o
repositório: **Pull and redeploy**.

## Instalação com docker compose

```bash
cd docker/ansible-semaphore
cp .env.example .env              # preencher
./preparar.sh
# copiar a chave privada e .vault_pass para secrets/ e os playbooks para workspace/
docker compose up -d --build
docker compose logs -f ansible
```

## Utilização

```bash
ssh -p 2222 <SSH_USER>@<anfitrião>
cd ~/workspace/ansible
ansible all -m ping
ansible-playbook playbook/<playbook>.yml
```

O `ansible.cfg` fica em `/etc/ansible/ansible.cfg` e a password do vault em `/etc/ansible/.vault_pass`
(`ANSIBLE_VAULT_PASSWORD_FILE` definido no login). Só os utilizadores do grupo `ansible` (principal e extra) a leem.

**Semaphore** (`http://<anfitrião>:3000`, utilizador `SEMAPHORE_ADMIN`):

| Onde | O quê |
|---|---|
| Key Store | Credencial **SSH** com o utilizador de destino e a chave privada (o Semaphore não usa a do contentor `ansible`) |
| Repositories | Tipo local, caminho `/playbooks/ansible` (ou o URL Git dos playbooks) |
| Inventory | Ficheiro do repositório ou estático, associado à credencial SSH |
| Task Templates | O playbook a correr |

## Notas

- As chaves privadas nunca entram na imagem nem no Git: são copiadas de `secrets/` em cada arranque.
- Ficheiros em falta em `secrets/` dão **AVISO** nos logs e o contentor arranca na mesma (sem ciclo de *restarting*).
- O contentor `ansible` corre como root (o `sshd` precisa); as sessões SSH são do utilizador principal ou do extra. Não
  usar `user:` no compose.
- Portas 2222 e 3000 só na rede local/VPN; para o Semaphore de fora, proxy reverso com HTTPS.
- `SEMAPHORE_ACCESS_KEY_ENCRYPTION` cifra as credenciais na base de dados: sem ela não se recuperam depois de um restauro.

## Verificação

```bash
docker logs ansible                                    # utilizadores, chave, ansible.cfg, vault, versão do Ansible
docker exec ansible ls -la /etc/ansible
docker exec -u <SSH_USER> ansible ansible --version
docker exec semaphore ls /playbooks
```
