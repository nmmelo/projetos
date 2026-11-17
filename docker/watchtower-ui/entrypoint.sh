#!/bin/bash
set -e

# Nome do utilizador criado na imagem (sem espaços)
SSH_USER="$(cat /etc/portal-user)"
export SSH_USER

: "${WATCHTOWER_HTTP_API_TOKEN:?WATCHTOWER_HTTP_API_TOKEN não definido}"
[ -n "${WEBHOOK_TOKEN:-}" ] || echo "AVISO: WEBHOOK_TOKEN vazio; os relatórios do Watchtower vão ser recusados"

# Pastas de dados (volume DATA_DIR): base de dados e logs, do utilizador que corre o portal
mkdir -p /data/db /data/logs
chown "${SSH_USER}:${SSH_USER}" /data/db /data/logs
chown -R "${SSH_USER}:${SSH_USER}" /data/db 2>/dev/null || true

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

# Acesso (só leitura) ao socket do Docker: o utilizador do portal entra no grupo dono do socket
if [ -S /var/run/docker.sock ]; then
    gid=$(stat -c %g /var/run/docker.sock)
    grp=$(getent group "$gid" | cut -d: -f1)
    if [ -z "$grp" ]; then groupadd -g "$gid" dockersock; grp=dockersock; fi
    usermod -aG "$grp" "$SSH_USER"
    echo "docker: socket com GID $gid ($grp)"
else
    echo "AVISO: /var/run/docker.sock não montado; sem lista de contentores nem histórico"
fi

# Rotação simples dos logs do nginx (10 MB, 1 cópia)
(
    while sleep 3600; do
        for f in /data/logs/nginx-access.log /data/logs/nginx-error.log; do
            if [ -f "$f" ] && [ "$(stat -c %s "$f")" -gt 10485760 ]; then
                mv -f "$f" "$f.1" && nginx -s reopen
            fi
        done
    done
) &

# Portal (Gunicorn) como o utilizador SSH; reinicia sozinho se terminar
(
    while true; do
        runuser -u "${SSH_USER}" -- gunicorn --chdir /opt/portal --bind 127.0.0.1:8000 \
            --workers 1 --threads 8 --timeout 120 --error-logfile - app:app || true
        echo "AVISO: portal terminou; a reiniciar em 5 s"
        sleep 5
    done
) &

nginx -t
exec nginx -g 'daemon off;'
