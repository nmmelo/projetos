#!/bin/bash
# entrypoint.sh — prepara o contentor ansible em cada arranque e fica a correr o sshd.
# Tudo pode vir das variáveis da stack do Portainer; ficheiros em /secrets (mount opcional) têm prioridade.
# Ficheiros ou variáveis em falta dão aviso e não param o contentor (evita o ciclo de "restarting").
set -euo pipefail

SSH_USER="$(cat /etc/ansible-user)"
export SSH_USER
SSH_HOME=$(getent passwd "$SSH_USER" | cut -d: -f6)
WORKSPACE="$SSH_HOME/workspace"
SECRETS=/secrets
KEY_NAME="${ANSIBLE_KEY_FILE:-id_ansible}"
aviso() { echo "AVISO: $*" >&2; }
b64() { printf '%s' "$1" | tr -d '[:space:]' | base64 -d; }

# ---------------------------------------------------------------- Host keys SSH (volume hostkeys)
mkdir -p /etc/ssh/keys /run/sshd
for tipo in ed25519 rsa; do
    chave="/etc/ssh/keys/ssh_host_${tipo}_key"
    [ -s "$chave" ] || ssh-keygen -q -t "$tipo" -N "" -f "$chave"
    install -m 600 -o root -g root "$chave"     "/etc/ssh/ssh_host_${tipo}_key"
    install -m 644 -o root -g root "$chave.pub" "/etc/ssh/ssh_host_${tipo}_key.pub"
done

# ---------------------------------------------------------------- Utilizadores SSH e chaves públicas
/usr/local/sbin/ssh-utilizadores.sh
UTILIZADORES="$SSH_USER"
if [ -n "${SSH_EXTRA_USER:-}" ] && id -u "$SSH_EXTRA_USER" >/dev/null 2>&1; then
    usermod -aG ansible "$SSH_EXTRA_USER"
    UTILIZADORES="$UTILIZADORES $SSH_EXTRA_USER"
fi

# ---------------------------------------------------------------- Chave privada das ligações de saída
# Origem: /secrets/<ANSIBLE_KEY_FILE> ou ANSIBLE_PRIVATE_KEY_B64 (base64 numa linha).
# Fica em ~/.ssh/<ANSIBLE_KEY_FILE> do utilizador principal e do extra.
tmpkey=$(mktemp)
if [ -s "$SECRETS/$KEY_NAME" ]; then
    cat "$SECRETS/$KEY_NAME" > "$tmpkey"
elif [ -n "${ANSIBLE_PRIVATE_KEY_B64:-}" ]; then
    b64 "$ANSIBLE_PRIVATE_KEY_B64" > "$tmpkey" 2>/dev/null || aviso "ANSIBLE_PRIVATE_KEY_B64 não é base64 válido"
fi
if [ -s "$tmpkey" ]; then
    grep -q "PRIVATE KEY" "$tmpkey" || aviso "a chave privada não parece válida (falta 'PRIVATE KEY')"
    for u in $UTILIZADORES; do
        home=$(getent passwd "$u" | cut -d: -f6); g=$(id -gn "$u")
        install -d -m 700 -o "$u" -g "$g" "$home/.ssh"
        install -m 600 -o "$u" -g "$g" "$tmpkey" "$home/.ssh/$KEY_NAME"
        ssh-keygen -y -P "" -f "$home/.ssh/$KEY_NAME" > "$home/.ssh/$KEY_NAME.pub" 2>/dev/null \
            && chown "$u:$g" "$home/.ssh/$KEY_NAME.pub" || rm -f "$home/.ssh/$KEY_NAME.pub"
    done
    echo "ansible: chave privada instalada em ~/.ssh/$KEY_NAME ($UTILIZADORES)"
else
    aviso "sem chave privada (ANSIBLE_PRIVATE_KEY_B64 vazio); o Ansible não consegue ligar aos servidores"
fi
rm -f "$tmpkey"

# ---------------------------------------------------------------- ansible.cfg
# Prioridade: /secrets/ansible.cfg → ANSIBLE_CFG_B64 → gerado a partir das variáveis ANSIBLE_*
if [ -s "$SECRETS/ansible.cfg" ]; then
    install -m 644 "$SECRETS/ansible.cfg" /etc/ansible/ansible.cfg
    echo "ansible: ansible.cfg de $SECRETS"
elif [ -n "${ANSIBLE_CFG_B64:-}" ] && b64 "$ANSIBLE_CFG_B64" > /etc/ansible/ansible.cfg 2>/dev/null; then
    chmod 644 /etc/ansible/ansible.cfg
    echo "ansible: ansible.cfg de ANSIBLE_CFG_B64"
else
    remote_user="${ANSIBLE_REMOTE_USER:-${SSH_EXTRA_USER:-$SSH_USER}}"
    pasta="${PLAYBOOKS_GIT_DIR:-ansible}"
    sed -e '/^#/d' -e "s#@REMOTE_USER@#$remote_user#" \
        -e "s#@KEY_NAME@#$KEY_NAME#" \
        -e "s#@INVENTORY@#${ANSIBLE_INVENTORY:-~/workspace/$pasta/inventory}#" \
        -e "s#@ROLES@#${ANSIBLE_ROLES_PATH:-~/workspace/$pasta/roles}#" \
        /etc/ansible/ansible.cfg.modelo > /etc/ansible/ansible.cfg
    chmod 644 /etc/ansible/ansible.cfg
    echo "ansible: ansible.cfg gerado (remote_user=$remote_user, chave ~/.ssh/$KEY_NAME)"
fi

# ---------------------------------------------------------------- Password do vault (só grupo "ansible")
rm -f /etc/profile.d/ansible.sh /etc/ansible/.vault_pass
if [ -s "$SECRETS/.vault_pass" ]; then
    install -m 640 -o root -g ansible "$SECRETS/.vault_pass" /etc/ansible/.vault_pass
elif [ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
    printf '%s\n' "$ANSIBLE_VAULT_PASSWORD" > /etc/ansible/.vault_pass
    chown root:ansible /etc/ansible/.vault_pass; chmod 640 /etc/ansible/.vault_pass
fi
if [ -s /etc/ansible/.vault_pass ]; then
    echo 'export ANSIBLE_VAULT_PASSWORD_FILE=/etc/ansible/.vault_pass' > /etc/profile.d/ansible.sh
    echo "ansible: password do vault instalada"
else
    aviso "sem password do vault; os playbooks com vault pedem a password"
fi

# ---------------------------------------------------------------- Playbooks (volume/mount em ~/workspace)
mkdir -p "$WORKSPACE"
# Pasta criada pelo Docker fica do root: passa para o utilizador (só a raiz, não mexe no conteúdo)
[ "$(stat -c %u "$WORKSPACE")" = "0" ] && chown "$SSH_USER:$(id -gn "$SSH_USER")" "$WORKSPACE"

# Credenciais do Git (repositório privado): ~/.git-credentials, nunca no URL guardado no .git/config
rm -f "$SSH_HOME/.git-credentials"
if [ -n "${PLAYBOOKS_GIT_URL:-}" ] && [ -n "${PLAYBOOKS_GIT_TOKEN:-}" ]; then
    servidor=$(printf '%s' "$PLAYBOOKS_GIT_URL" | sed -E 's#^(https://[^/]+).*#\1#')
    printf '%s\n' "${servidor/https:\/\//https://${PLAYBOOKS_GIT_USER:-git}:${PLAYBOOKS_GIT_TOKEN}@}" > "$SSH_HOME/.git-credentials"
    chown "$SSH_USER:" "$SSH_HOME/.git-credentials"; chmod 600 "$SSH_HOME/.git-credentials"
    runuser -u "$SSH_USER" -- git config --global credential.helper store
fi

# Clone no 1.º arranque (se a pasta ainda não existir); depois: git pull dentro do contentor
if [ -n "${PLAYBOOKS_GIT_URL:-}" ]; then
    destino="$WORKSPACE/${PLAYBOOKS_GIT_DIR:-ansible}"
    if [ ! -d "$destino/.git" ]; then
        echo "playbooks: git clone de $PLAYBOOKS_GIT_URL (${PLAYBOOKS_GIT_BRANCH:-omissão})"
        runuser -u "$SSH_USER" -- git clone ${PLAYBOOKS_GIT_BRANCH:+-b "$PLAYBOOKS_GIT_BRANCH"} \
            "$PLAYBOOKS_GIT_URL" "$destino" || aviso "git clone falhou (URL, branch ou token)"
    else
        echo "playbooks: $destino já existe (atualizar com git pull)"
    fi
fi
[ -n "$(ls -A "$WORKSPACE" 2>/dev/null)" ] || aviso "$WORKSPACE vazio (definir PLAYBOOKS_GIT_URL ou copiar os playbooks)"

ansible --version | head -1
/usr/sbin/sshd -t
exec /usr/sbin/sshd -D -e
