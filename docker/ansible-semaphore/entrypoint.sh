#!/bin/bash
# entrypoint.sh — prepara o contentor ansible em cada arranque e fica a correr o sshd.
# Ficheiros em falta dão aviso e não param o contentor (evita o ciclo de "restarting").
set -euo pipefail

SSH_USER="$(cat /etc/ansible-user)"
export SSH_USER
SECRETS=/secrets
aviso() { echo "AVISO: $*" >&2; }

# ---------------------------------------------------------------- Host keys SSH
# Guardadas no mount (hostkeys/), usadas a partir de uma cópia interna:
# alguns volumes (ex. Synology) forçam permissões que o sshd rejeita.
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
# ANSIBLE_KEY_FILE = nome do ficheiro em secrets/; fica em ~/.ssh/<nome> de cada utilizador
# (o ansible.cfg aponta para ela em private_key_file).
if [ -n "${ANSIBLE_KEY_FILE:-}" ]; then
    origem="$SECRETS/$ANSIBLE_KEY_FILE"
    if [ -s "$origem" ]; then
        for u in $UTILIZADORES; do
            home=$(getent passwd "$u" | cut -d: -f6)
            install -d -m 700 -o "$u" -g "$(id -gn "$u")" "$home/.ssh"
            install -m 600 -o "$u" -g "$(id -gn "$u")" "$origem" "$home/.ssh/$ANSIBLE_KEY_FILE"
            [ -s "$origem.pub" ] && install -m 644 -o "$u" -g "$(id -gn "$u")" "$origem.pub" "$home/.ssh/$ANSIBLE_KEY_FILE.pub"
            echo "ansible: chave $ANSIBLE_KEY_FILE instalada em $u"
        done
    else
        aviso "$origem não existe ou está vazio; sem chave privada para as ligações de saída"
    fi
fi

# ---------------------------------------------------------------- ansible.cfg e password do vault
# Copiados para /etc/ansible: o ansible.cfg legível por todos, a password só pelo grupo "ansible".
if [ -s "$SECRETS/ansible.cfg" ]; then
    install -m 644 -o root -g root "$SECRETS/ansible.cfg" /etc/ansible/ansible.cfg
    echo "ansible: ansible.cfg instalado"
else
    aviso "$SECRETS/ansible.cfg em falta; o Ansible usa a configuração por omissão"
fi
rm -f /etc/profile.d/ansible.sh
if [ -s "$SECRETS/.vault_pass" ]; then
    install -m 640 -o root -g ansible "$SECRETS/.vault_pass" /etc/ansible/.vault_pass
    echo 'export ANSIBLE_VAULT_PASSWORD_FILE=/etc/ansible/.vault_pass' > /etc/profile.d/ansible.sh
    echo "ansible: password do vault instalada"
else
    aviso "$SECRETS/.vault_pass em falta; os playbooks com vault pedem a password"
fi

# ---------------------------------------------------------------- Playbooks
[ -d "/home/$SSH_USER/workspace" ] || aviso "/home/$SSH_USER/workspace não montado (PLAYBOOKS_DIR)"

ansible --version | head -1
/usr/sbin/sshd -t
exec /usr/sbin/sshd -D -e
