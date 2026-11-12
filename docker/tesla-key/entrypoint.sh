#!/bin/bash
set -e
HOME_DIR="/home/${SSH_USER}"

# Host keys: guardadas no volume, mas usadas a partir de uma cópia interna
# (os volumes do Synology forçam permissões 0755 que o sshd rejeita)
mkdir -p /etc/ssh/keys
[ -f /etc/ssh/keys/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N "" -f /etc/ssh/keys/ssh_host_ed25519_key
install -m 600 -o root -g root /etc/ssh/keys/ssh_host_ed25519_key     /etc/ssh/ssh_host_ed25519_key
install -m 644 -o root -g root /etc/ssh/keys/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ed25519_key.pub
grep -q "^HostKey /etc/ssh/ssh_host_ed25519_key" /etc/ssh/sshd_config || \
    echo "HostKey /etc/ssh/ssh_host_ed25519_key" >> /etc/ssh/sshd_config

# Importa a chave pública SSH para o authorized_keys
mkdir -p "${HOME_DIR}/.ssh"
if [ -f /import/authorized_keys ]; then
    grep -v '^\s*#' /import/authorized_keys | grep -v '^\s*$' > "${HOME_DIR}/.ssh/authorized_keys" || true
fi
chmod 700 "${HOME_DIR}/.ssh"
chmod 600 "${HOME_DIR}/.ssh/authorized_keys" 2>/dev/null || true
chown -R "${SSH_USER}:${SSH_USER}" "${HOME_DIR}/.ssh"

# Pasta da chave Tesla editável pelo utilizador SSH
mkdir -p /var/www/html/.well-known/appspecific
chown -R "${SSH_USER}:${SSH_USER}" /var/www/html 2>/dev/null || true

# Pasta da configuração/chaves/tokens Tesla (volume ./tesla)
mkdir -p /opt/tesla
chown -R "${SSH_USER}:${SSH_USER}" /opt/tesla 2>/dev/null || true

/usr/sbin/sshd
exec nginx -g 'daemon off;'
