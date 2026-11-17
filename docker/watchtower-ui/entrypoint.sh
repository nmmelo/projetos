#!/bin/bash
set -e

: "${WATCHTOWER_HOST:=watchtower}"
: "${WATCHTOWER_PORT:=8080}"
: "${WATCHTOWER_HTTP_API_TOKEN:?WATCHTOWER_HTTP_API_TOKEN não definido}"
export WATCHTOWER_HOST WATCHTOWER_PORT WATCHTOWER_HTTP_API_TOKEN

# Host keys: guardadas no volume, mas usadas a partir de uma cópia interna
# (alguns volumes, ex. Synology, forçam permissões 0755 que o sshd rejeita)
mkdir -p /etc/ssh/keys
[ -f /etc/ssh/keys/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N "" -f /etc/ssh/keys/ssh_host_ed25519_key
install -m 600 -o root -g root /etc/ssh/keys/ssh_host_ed25519_key     /etc/ssh/ssh_host_ed25519_key
install -m 644 -o root -g root /etc/ssh/keys/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ed25519_key.pub
grep -q "^HostKey /etc/ssh/ssh_host_ed25519_key" /etc/ssh/sshd_config || \
    echo "HostKey /etc/ssh/ssh_host_ed25519_key" >> /etc/ssh/sshd_config

# Utilizadores SSH (principal + extra opcional) e chaves públicas (ficheiro e/ou variáveis)
/usr/local/sbin/ssh-utilizadores.sh

/usr/sbin/sshd -t && /usr/sbin/sshd || echo "ERRO: configuração do sshd inválida; SSH indisponível"

# Configuração do nginx a partir do template (só estas variáveis; as do nginx, ex. $uri, ficam intactas)
envsubst '${WATCHTOWER_HOST} ${WATCHTOWER_PORT} ${WATCHTOWER_HTTP_API_TOKEN}' \
    < /etc/nginx/watchtower-ui.conf.template > /etc/nginx/sites-available/default
chmod 600 /etc/nginx/sites-available/default   # contém o token
nginx -t

exec nginx -g 'daemon off;'
