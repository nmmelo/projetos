# ansible-semaphore

Dois contentores para correr playbooks Ansible, instalados **só pelo Portainer** (stack a partir do Git), sem SSH no
anfitrião nem pastas criadas à mão:

| Serviço | Imagem | O que faz |
|---|---|---|
| `ansible` | Ubuntu 26.04 (build no deploy) | Ansible + Git + SSH. Utilizador principal com sudo (só chave pública), utilizador extra opcional, chave privada para os servidores, `ansible.cfg`, password do vault e clone dos playbooks |
| `semaphore` | `semaphoreui/semaphore` | Interface web do Ansible (SQLite). Vê os mesmos playbooks em `/playbooks` (só leitura) |

Nada sensível fica no Git: chaves, password do vault, token e passwords vão nas variáveis da stack.

## Arquitetura

```
ssh ──► :2222 ─► ansible ─► ~/workspace ◄── volume "playbooks" (ou PLAYBOOKS_DIR) ──┐
                   │            └── git clone PLAYBOOKS_GIT_URL (1.º arranque)     │
                   └─► ssh <ANSIBLE_REMOTE_USER>@servidores                        │
web ──► :3000 ─► semaphore ─► /playbooks (só leitura) ◄────────────────────────────┘
```

## Ficheiros

```
ansible-semaphore/
├── Dockerfile              # Ubuntu 26.04 + ansible + git + openssh + sudo
├── docker-compose.yml      # ansible + semaphore, tudo por variáveis, volumes nomeados
├── entrypoint.sh           # host keys, utilizadores, chave privada, ansible.cfg, vault, clone, sshd
├── ssh-utilizadores.sh     # utilizador principal + extra, chaves públicas e sudo em cada arranque
├── ansible.cfg.modelo      # base do ansible.cfg gerado
├── .env.example            # lista das variáveis (carregar no Portainer)
└── secrets/README.md       # alternativa opcional por ficheiros
```

## Volumes

| Volume | Contentor | Conteúdo |
|---|---|---|
| `playbooks` (ou `PLAYBOOKS_DIR`) | `ansible:/home/<SSH_USER>/workspace`, `semaphore:/playbooks` (ro) | Playbooks, inventário, roles |
| `hostkeys` | `ansible:/etc/ssh/keys` | Host keys SSH (a impressão digital não muda ao recriar) |
| `semaphore_data` | `semaphore:/var/lib/semaphore` | Base de dados SQLite |
| `semaphore_config` | `semaphore:/etc/semaphore` | Configuração |
| `semaphore_tmp` | `semaphore:/tmp/semaphore` | Clones dos repositórios das tarefas |

Com `PLAYBOOKS_DIR=/volume1/docker/ansible-semaphore/workspace` os playbooks ficam numa pasta do Synology (criada pelo
Docker, visível no File Station) em vez do volume.

## Variáveis

| Variável | Omissão | Descrição |
|---|---|---|
| `SSH_USER` | `admin` | Utilizador principal (sudo, só chave) — build |
| `SSH_UID` / `SSH_GID` | `1026` / `100` | Iguais aos do anfitrião (`id <utilizador>`) — build |
| `SSH_PUBKEY` | | Chave(s) pública(s) do principal; várias separadas por `;` |
| `SSH_EXTRA_USER` | | Utilizador extra (ex. o de automação nos servidores de destino) |
| `SSH_EXTRA_PUBKEY` | | Chave(s) pública(s) do extra; vazio = não entra por SSH |
| `SSH_EXTRA_SUDO` / `SSH_EXTRA_UID` | `1` / | Sudo sem password do extra; UID opcional |
| `ANSIBLE_PRIVATE_KEY_B64` | | Chave privada para os servidores, em base64 numa linha (`base64 -w0 <chave>`) |
| `ANSIBLE_KEY_FILE` | `id_ansible` | Nome da chave em `~/.ssh/` (principal e extra) |
| `ANSIBLE_REMOTE_USER` | `SSH_EXTRA_USER` | Utilizador nos servidores de destino |
| `ANSIBLE_INVENTORY` / `ANSIBLE_ROLES_PATH` | `~/workspace/<dir>/inventory` / `.../roles` | Caminhos no ansible.cfg gerado |
| `ANSIBLE_CFG_B64` | | ansible.cfg próprio em base64 (substitui o gerado) |
| `ANSIBLE_VAULT_PASSWORD` | | Password do Ansible Vault |
| `PLAYBOOKS_DIR` | volume `playbooks` | Caminho absoluto no anfitrião, se quiser os playbooks numa pasta |
| `PLAYBOOKS_GIT_URL` / `_BRANCH` | | Repositório dos playbooks, clonado no 1.º arranque |
| `PLAYBOOKS_GIT_DIR` | `ansible` | Pasta do clone em `~/workspace` |
| `PLAYBOOKS_GIT_USER` / `_TOKEN` | `git` / | Token para repositório privado (fica em `~/.git-credentials`, não no URL) |
| `SEMAPHORE_VERSION` | `v2.18.31` | Tag da imagem |
| `SEMAPHORE_ADMIN` / `_NAME` | `admin` / `Administrador` | Administrador (só no 1.º arranque) |
| `SEMAPHORE_ADMIN_EMAIL` / `_PASSWORD` | — | Obrigatórios |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | — | Obrigatório: `openssl rand -base64 32`. Guardar; não mudar depois |
| `SSH_PORT` / `SEMAPHORE_PORT` | `2222` / `3000` | Portas no anfitrião |
| `ANSIBLE_CONTAINER` / `SEMAPHORE_CONTAINER` | `ansible` / `semaphore` | Nomes dos contentores |

## Instalação no Portainer

**1. Apagar a instalação antiga** (se existir): Stacks → stack antiga → **Delete this stack**; **Volumes** → apagar os
volumes antigos (`*_ssh_hostkeys`, `*_semaphore_*`); **Images** → apagar a imagem local antiga.

**2. Preparar os valores** (num PC, uma vez):

```bash
base64 -w0 chave-privada            # → ANSIBLE_PRIVATE_KEY_B64   (macOS: base64 -i chave-privada)
cat chave.pub                       # → SSH_PUBKEY
openssl rand -base64 32             # → SEMAPHORE_ACCESS_KEY_ENCRYPTION
```

No Windows (PowerShell): `[Convert]::ToBase64String([IO.File]::ReadAllBytes("chave-privada"))`.

**3. Criar a stack**: Stacks → **Add stack** → **Repository**

| Campo | Valor |
|---|---|
| Repository URL | URL deste repositório (privado: ativar **Authentication** com utilizador e token) |
| Repository reference | `refs/heads/main` |
| Compose path | `docker/ansible-semaphore/docker-compose.yml` |
| Environment variables | **Load variables from .env file** com o `.env.example` preenchido, ou uma a uma |

**Deploy the stack.** A imagem `ansible` é construída no deploy (`pull_policy: build`).

**4. Ver se arrancou**: Containers → `ansible` → **Logs**. Deve mostrar os utilizadores, a chave instalada, o
ansible.cfg, o vault, o clone dos playbooks e a versão do Ansible. As linhas **AVISO** dizem o que falta.

**Alterações depois**: Stacks → stack → mudar variáveis → **Update the stack** (ou **Pull and redeploy** para
reconstruir com a versão nova do repositório). Os volumes mantêm-se.

## Utilização

**Consola sem SSH**: Containers → `ansible` → **Console** → *Command* `/bin/bash`, *User* `<SSH_USER>`.

```bash
cd ~/workspace/ansible
git pull                            # atualizar os playbooks
ansible all -m ping
ansible-playbook playbook/<playbook>.yml
```

Por SSH: `ssh -p 2222 <SSH_USER>@<anfitrião>` (ou VS Code Remote-SSH).

**Semaphore** (`http://<anfitrião>:3000`, utilizador `SEMAPHORE_ADMIN`):

| Onde | O quê |
|---|---|
| Key Store | Credencial **SSH** com o utilizador de destino e a chave privada (o Semaphore não usa a do contentor `ansible`) |
| Repositories | URL Git dos playbooks, ou local com o caminho `/playbooks/ansible` |
| Inventory | Ficheiro do repositório ou estático, associado à credencial SSH |
| Task Templates | O playbook a correr |

## Notas

- As variáveis da stack ficam visíveis a quem administra o Portainer e em `docker inspect`. Proteger o acesso ao
  Portainer (2FA, só rede local/VPN). Para não as ter em variáveis, ver `secrets/README.md`.
- A chave privada e a password do vault são escritas em cada arranque e só os utilizadores do grupo `ansible` as leem.
- Variáveis em falta dão **AVISO** nos logs e o contentor arranca na mesma (sem ciclo de *restarting*).
- O contentor `ansible` corre como root (o `sshd` precisa); não usar `user:` no compose.
- O clone só é feito se a pasta ainda não existir; depois atualiza-se com `git pull`.
- Portas 2222 e 3000 só na rede local/VPN; para o Semaphore de fora, proxy reverso com HTTPS.
