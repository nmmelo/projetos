# Conversa: ansible-semaphore (Ansible + Semaphore no Portainer)

Chat de 18 de novembro de 2026. As perguntas estão resumidas; as respostas mantêm as conclusões. Os ficheiros finais
estão nesta pasta.

---

## 1. Contentor sempre em "restarting"

**Pergunta:** contentor com o erro `install: /run/sshd: chmod failed with error Operation not permitted (os error 1)`.

**Resposta:** o compose tinha `user: "${PUID}:${PGID}"`. O contentor corria como UID 1026, o entrypoint não conseguia
criar `/run/sshd` (dono root) e o `sshd` também precisa de root (porta 22, host keys, mudar de utilizador por sessão).
Com `restart: unless-stopped`, o ciclo repetia-se.

| Correção | Detalhe |
|---|---|
| Remover `user:` | O contentor corre como root; cada login SSH é do utilizador |
| UID/GID no Dockerfile | `useradd -u <UID> -g <GID>` para os ficheiros dos playbooks terem o dono certo no anfitrião |

Também havia um `useradd` duplicado do mesmo utilizador no Dockerfile (o build falhava).

## 2. Build no Portainer

Uma stack criada no editor web não tem a pasta do Dockerfile, por isso `build: .` não funciona. Opções: stack a partir
do Git (o Portainer faz o build no deploy) ou construir a imagem em Images → Build e usar só `image:`. Caminhos relativos
(`./workspace`) numa stack do Portainer apontam para a pasta interna do Portainer: usar caminhos absolutos.

Para aplicar uma imagem nova, os contentores têm de ser recriados (Update the stack / Recreate). Não misturar
`docker compose` por SSH com uma stack do Portainer (nomes de projeto diferentes, contentores duplicados).

## 3. Projeto para o repositório

**Pedido:** adaptar o projeto para o Portainer, ao estilo do watchtower-ui: chaves públicas, UID, utilizador extra e
caminho no anfitrião em variáveis; volumes e mounts garantidos (um deles com os playbooks); nada da pasta `secrets` no Git.

| Antes | Depois |
|---|---|
| Utilizadores fixos no Dockerfile | `SSH_USER`/`SSH_UID`/`SSH_GID` (build) + utilizador extra em cada arranque |
| Chaves públicas em `secrets:` do compose | `SSH_PUBKEY` / `SSH_EXTRA_PUBKEY` (variáveis) ou `secrets/authorized_keys` |
| Chave privada só no utilizador de automação | `ANSIBLE_KEY_FILE`, instalada no principal e no extra (o `ansible.cfg` usa `~/.ssh/<nome>`) |
| `./secrets/...` relativos | `DATA_DIR/secrets` montado em `/secrets` (só leitura) |
| Volumes nomeados | Pastas em `DATA_DIR` (host keys, dados do Semaphore) e `PLAYBOOKS_DIR` |
| `ANSIBLE_*` só no ambiente do contentor (não chegam às sessões SSH) | `/etc/ansible/ansible.cfg` e `ANSIBLE_VAULT_PASSWORD_FILE` em `/etc/profile.d` |
| Ficheiro em falta → erro → reinício em ciclo | Aviso nos logs, o contentor arranca |

O `ansible.cfg.example` corrige a secção `[privilege_scalation]` → `[privilege_escalation]` (com o nome errado, as
opções `become` eram ignoradas) e retira opções obsoletas (`sudo_user`, `ask-sudo_pass`, `command_warnings`).
