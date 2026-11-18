# ansible-semaphore

Dois contentores para correr playbooks Ansible, instalados **só pelo Portainer** (stack a partir do Git), sem SSH no
anfitrião:

| Serviço | Imagem | O que faz |
|---|---|---|
| `ansible` | Ubuntu 26.04 (build no deploy) | Ansible + Git + SSH. Utilizador principal com sudo (só chave pública), utilizador extra opcional, chave privada para os servidores, `ansible.cfg`, password do vault e clone dos playbooks |
| `semaphore` | `semaphoreui/semaphore` | Interface web do Ansible (SQLite). Vê os mesmos playbooks em `/playbooks` (só leitura) |

Nada sensível nem nenhum nome de utilizador fica no Git: tudo vem das variáveis da stack ou de ficheiros carregados no
anfitrião.

Nos exemplos: `<utilizador>` = utilizador principal, `<utilizador-extra>` = utilizador de automação (o que existe nos
servidores de destino), `<chave>` = nome do ficheiro da chave privada, `<nas>` = endereço do anfitrião.

## Arquitetura

```
ssh ──► :2222 ─► ansible ─► ~/workspace ◄── volume "playbooks" (ou PLAYBOOKS_DIR) ──┐
                   │   └─ /secrets (SECRETS_DIR, opcional)                         │
                   │            └── git clone PLAYBOOKS_GIT_URL (1.º arranque)     │
                   └─► ssh <utilizador-extra>@servidores                           │
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
├── .env.example            # lista das variáveis
└── secrets/README.md       # ficheiros que podem ir para SECRETS_DIR
```

## Volumes

| Volume | Contentor | Conteúdo |
|---|---|---|
| `playbooks` (ou `PLAYBOOKS_DIR`) | `ansible:/home/<utilizador>/workspace`, `semaphore:/playbooks` (ro) | Playbooks, inventário, roles |
| `secrets` (ou `SECRETS_DIR`) | `ansible:/secrets` (ro) | Chave privada, `.vault_pass`, `ansible.cfg` (opcional) |
| `hostkeys` | `ansible:/etc/ssh/keys` | Host keys SSH (a impressão digital não muda ao recriar) |
| `semaphore_data` | `semaphore:/var/lib/semaphore` | Base de dados SQLite |
| `semaphore_config` | `semaphore:/etc/semaphore` | Configuração |
| `semaphore_tmp` | `semaphore:/tmp/semaphore` | Clones dos repositórios das tarefas |

`PLAYBOOKS_DIR` e `SECRETS_DIR` vazios = volumes do Docker. Com um caminho absoluto (ex.
`/volume1/docker/ansible-semaphore/workspace`) passam a ser pastas do anfitrião, visíveis no File Station.

## Variáveis

| Variável | Omissão | Descrição |
|---|---|---|
| `SSH_USER` | — | **Obrigatório.** Utilizador principal (sudo, só chave). Evitar nomes óbvios (`admin`, `root`) |
| `SSH_UID` / `SSH_GID` | `1026` / `100` | Iguais aos do anfitrião (`id <utilizador>` no Synology) |
| `SSH_PUBKEY` | | Chave(s) pública(s) do principal (conteúdo do `.pub`) |
| `SSH_EXTRA_USER` | | Utilizador extra, ex. o de automação que existe nos servidores de destino |
| `SSH_EXTRA_PUBKEY` | | Chave(s) pública(s) do extra, para entrar no contentor com ele; vazio = não entra por SSH |
| `SSH_EXTRA_SUDO` / `SSH_EXTRA_UID` | `1` / | Sudo sem password do extra; UID opcional |
| `SECRETS_DIR` | volume vazio | Pasta com ficheiros carregados por upload; prioridade sobre as variáveis abaixo |
| `ANSIBLE_KEY_FILE` | `id_ansible` | Nome da chave privada: do ficheiro em `SECRETS_DIR` e em `~/.ssh/` |
| `ANSIBLE_PRIVATE_KEY_B64` | | Chave privada em base64 numa linha (alternativa ao ficheiro) |
| `ANSIBLE_VAULT_PASSWORD` | | Password do Ansible Vault (alternativa ao `.vault_pass`) |
| `ANSIBLE_REMOTE_USER` | `SSH_EXTRA_USER` | Utilizador nos servidores de destino |
| `ANSIBLE_INVENTORY` / `ANSIBLE_ROLES_PATH` | `~/workspace/<dir>/inventory` / `.../roles` | Caminhos no ansible.cfg gerado |
| `ANSIBLE_CFG_B64` | | ansible.cfg próprio em base64 (alternativa ao ficheiro) |
| `PLAYBOOKS_DIR` | volume `playbooks` | Caminho absoluto no anfitrião para os playbooks |
| `PLAYBOOKS_GIT_URL` / `_BRANCH` | | Repositório dos playbooks, clonado no 1.º arranque |
| `PLAYBOOKS_GIT_DIR` | `ansible` | Pasta do clone em `~/workspace` |
| `PLAYBOOKS_GIT_USER` / `_TOKEN` | `git` / | Token para repositório privado (fica em `~/.git-credentials`, não no URL) |
| `SEMAPHORE_VERSION` | `v2.18.31` | Tag da imagem |
| `SEMAPHORE_ADMIN` / `_NAME` | — | **Obrigatórios.** Conta de gestão do Semaphore (só no 1.º arranque) |
| `SEMAPHORE_ADMIN_EMAIL` / `_PASSWORD` | — | **Obrigatórios** |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | — | **Obrigatório**: `openssl rand -base64 32`. Guardar; não mudar depois |
| `SSH_PORT` / `SEMAPHORE_PORT` | `2222` / `3000` | Portas no anfitrião |
| `ANSIBLE_CONTAINER` / `SEMAPHORE_CONTAINER` | `ansible` / `semaphore` | Nomes dos contentores |
| `TZ` | `Europe/Lisbon` | Fuso horário |

### Chaves e passwords: o que é cada uma

| Item | O que é | Onde vai |
|---|---|---|
| Chave pública do principal (`<utilizador>.pub`) | Deixa entrar no contentor por SSH | `SSH_PUBKEY` |
| Chave pública do extra (`<utilizador-extra>.pub`) | Deixa entrar no contentor com o extra (opcional). Tem de estar também no `authorized_keys` do extra em cada servidor de destino | `SSH_EXTRA_PUBKEY` |
| Chave privada do extra (`<chave>`) | O Ansible usa-a para entrar nos servidores de destino | Ficheiro em `SECRETS_DIR` **ou** `ANSIBLE_PRIVATE_KEY_B64` |
| Password do vault (`.vault_pass`) | Desencripta os ficheiros cifrados com `ansible-vault` | Ficheiro em `SECRETS_DIR` **ou** `ANSIBLE_VAULT_PASSWORD` |

O `.pub` da chave privada não é preciso: o contentor gera-o a partir da privada.

**Várias chaves públicas** para o mesmo utilizador: na mesma linha, separadas por `;`

```env
SSH_EXTRA_PUBKEY=ssh-ed25519 AAAA... pc;ssh-rsa AAAA... portatil
```

**Chave privada em base64** (só se não usar `SECRETS_DIR`), porque as variáveis do Portainer são de uma linha:

```bash
base64 -w0 <chave>                                                   # Linux
base64 -i <chave>                                                    # macOS
[Convert]::ToBase64String([IO.File]::ReadAllBytes("<chave>"))        # Windows (PowerShell)
```

O resultado começa por `LS0tLS1CRUdJTi`; o contentor converte-o de volta e grava-o em `~/.ssh/<chave>`.

## Instalação no Portainer

### 1. Preparar os valores (no PC)

| Valor | Como |
|---|---|
| Chave de cifra do Semaphore | `openssl rand -base64 32` |
| Token dos playbooks (repositório privado) | GitHub → Settings → Developer settings → **Fine-grained tokens** → só o repositório dos playbooks → *Contents: Read-only* |
| Token deste repositório (se for privado) | O mesmo, para este repositório (usado pelo Portainer) |

### 2. Instalação antiga: apagar ou desligar

Só desligar não chega: os nomes `ansible` e `semaphore` continuam ocupados e o deploy falha com
*container name already in use*.

| Opção | Passos |
|---|---|
| **Manter como backup** | Containers → selecionar os dois → **Stop** → abrir cada um → mudar o nome para `ansible-old` / `semaphore-old` |
| **Apagar** | Stacks → stack antiga → **Delete this stack** (ou Containers → **Remove**); **Volumes** → apagar os antigos; **Images** → apagar a imagem local antiga |

### 3. Ficheiros no NAS (File Station)

Opcional, mas mais seguro do que pôr a chave nas variáveis (que ficam visíveis no Portainer e em `docker inspect`).

1. Criar a pasta `docker/ansible-semaphore/secrets`.
2. Carregar:

| Ficheiro | Obrigatório |
|---|---|
| `<chave>` (chave privada, com o mesmo nome que `ANSIBLE_KEY_FILE`) | Sim |
| `.vault_pass` (começa por ponto: no Windows ativar *Itens ocultos*, no macOS `Cmd+Shift+.`) | Só com vault |
| `ansible.cfg` | Não (é gerado). Se for o próprio, a secção tem de ser `[privilege_escalation]` |

3. Botão direito na pasta → **Propriedades** → **Permissões** → acesso só ao utilizador que gere o NAS.

### 4. Criar a stack

Stacks → **Add stack**

| Campo | Valor |
|---|---|
| Name | `ansible-semaphore` |
| Build method | **Repository** |
| Authentication | Ligar se este repositório for privado (utilizador GitHub + token) |
| Repository URL | URL deste repositório |
| Repository reference | `refs/heads/main` |
| Compose path | `docker/ansible-semaphore/docker-compose.yml` |
| Environment variables | **Advanced mode** → colar o bloco abaixo |

```env
SECRETS_DIR=/volume1/docker/ansible-semaphore/secrets
SSH_USER=<utilizador>
SSH_UID=1026
SSH_GID=100
SSH_PUBKEY=<conteúdo de <utilizador>.pub>
SSH_EXTRA_USER=<utilizador-extra>
SSH_EXTRA_PUBKEY=<conteúdo de <utilizador-extra>.pub>
SSH_EXTRA_SUDO=1
ANSIBLE_KEY_FILE=<chave>
ANSIBLE_INVENTORY=~/workspace/ansible/<inventário>
PLAYBOOKS_GIT_URL=<URL do repositório dos playbooks>
PLAYBOOKS_GIT_BRANCH=<branch>
PLAYBOOKS_GIT_DIR=ansible
PLAYBOOKS_GIT_TOKEN=<token>
SEMAPHORE_ADMIN=<conta>
SEMAPHORE_ADMIN_NAME=<nome>
SEMAPHORE_ADMIN_EMAIL=<email>
SEMAPHORE_ADMIN_PASSWORD=<password forte>
SEMAPHORE_ACCESS_KEY_ENCRYPTION=<resultado do openssl>
SSH_PORT=2222
SEMAPHORE_PORT=3000
TZ=Europe/Lisbon
```

Regras do Advanced mode: uma variável por linha, sem aspas, sem comentários, sem espaços no fim. Valores com espaços
(nome, chave pública) ficam como estão. Sem `SECRETS_DIR`, juntar `ANSIBLE_PRIVATE_KEY_B64` e `ANSIBLE_VAULT_PASSWORD`.
Em alternativa: preencher o `.env.example` e usar **Load variables from .env file**.

**Deploy the stack.** O 1.º deploy demora uns minutos (build da imagem).

### 5. Confirmar

Containers → `ansible` → **Logs**:

```
ssh: <utilizador> com 1 chave(s)
ssh: <utilizador-extra> com 1 chave(s)
ansible: chave privada instalada em ~/.ssh/<chave> (<utilizador> <utilizador-extra>)
ansible: ansible.cfg gerado (remote_user=<utilizador-extra>, chave ~/.ssh/<chave>)
ansible: password do vault instalada
playbooks: git clone de <URL> (<branch>)
ansible [core 2.x.x]
```

Linhas **AVISO** = falta uma variável ou ficheiro; o contentor arranca na mesma. Corrigir e **Update the stack**.

### 6. Testar

Containers → `ansible` → **Console** → Command `/bin/bash`, User `<utilizador>` → **Connect**

```bash
cd ~/workspace/ansible
ansible all -m ping
ansible-playbook playbook/<playbook>.yml
```

Por SSH: `ssh -p 2222 <utilizador>@<nas>` (ou VS Code Remote-SSH).

### 7. Semaphore

`http://<nas>:3000`, conta `SEMAPHORE_ADMIN`:

| Menu | Ação |
|---|---|
| Key Store | **New Key** → SSH, utilizador `<utilizador-extra>`, colar a chave privada (o Semaphore não usa a do contentor `ansible`) |
| Repositories | Local `/playbooks/ansible`, ou o URL Git dos playbooks |
| Inventory | Ficheiro do repositório, com a key acima |
| Task Templates | Escolher o playbook → **Run** |

### 8. Limpar

Se a instalação antiga ficou como backup: com tudo a funcionar, remover `ansible-old` / `semaphore-old` e os volumes e
imagens antigos sem uso.

## Manutenção no Portainer

| Quero… | Onde |
|---|---|
| Mudar uma variável | Stacks → stack → Environment variables → **Update the stack** |
| Aplicar uma versão nova deste repositório | Stacks → stack → **Pull and redeploy** (reconstrói a imagem) |
| Atualizar os playbooks | Console do `ansible` → `cd ~/workspace/ansible && git pull` |
| Trocar a chave ou o `.vault_pass` | Substituir o ficheiro no File Station → Containers → `ansible` → **Restart** |
| Começar um contentor limpo | Containers → `ansible` → **Recreate** (ver abaixo) |

**Restart vs Recreate vs Update the stack**

| Ação | O que faz | Volumes |
|---|---|---|
| Restart | Reinicia o mesmo contentor | Mantêm-se |
| Recreate | Apaga e cria o contentor com a **mesma** configuração e imagem local (*Pull latest image* desligado para a imagem `ansible`) | Mantêm-se; o que estava fora dos volumes perde-se |
| Update the stack | Aplica variáveis, portas ou volumes alterados | Mantêm-se |
| Pull and redeploy | Lê o repositório, reconstrói a imagem e recria | Mantêm-se |

Em todos os casos o entrypoint volta a correr e reinstala chaves, `ansible.cfg` e vault.

## Notas

- O contentor `ansible` corre como root (o `sshd` precisa); as sessões SSH são do utilizador. Não usar `user:` no
  compose (o `sshd` não arranca e o contentor fica em *restarting*).
- A chave privada e a password do vault só são legíveis pelos utilizadores do grupo `ansible` (principal e extra).
- O clone só é feito se a pasta ainda não existir; depois atualiza-se com `git pull`.
- Portas 2222 e 3000 só na rede local/VPN; para o Semaphore de fora, proxy reverso com HTTPS.
- Proteger o acesso ao Portainer (2FA, só rede local/VPN): quem o administra vê as variáveis da stack.
- `SEMAPHORE_ACCESS_KEY_ENCRYPTION` cifra as credenciais na base de dados: sem ela não se recuperam depois de um restauro.
