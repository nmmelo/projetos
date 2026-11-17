#!/bin/bash
# preparar.sh — cria as pastas e ficheiros dos bind mounts em DATA_DIR antes do 1.º arranque.
# Necessário em sistemas onde o Docker não cria as pastas sozinho (ex. Synology).
# Uso: ./preparar.sh   (na pasta do projeto, com o .env já preenchido)
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "Falta o .env (cp .env.example .env)"; exit 1; }

valor() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/'; }

DATA_DIR=$(valor DATA_DIR); DATA_DIR=${DATA_DIR:-.}
SSH_UID=$(valor SSH_UID); SSH_UID=${SSH_UID:-1001}

echo "DATA_DIR = $DATA_DIR"
mkdir -p "$DATA_DIR/db" "$DATA_DIR/logs" "$DATA_DIR/hostkeys" "$DATA_DIR/ssh"
if [ ! -f "$DATA_DIR/ssh/authorized_keys" ]; then
    if [ -f ssh/authorized_keys.example ] && [ "$DATA_DIR" != "." ]; then
        cp ssh/authorized_keys.example "$DATA_DIR/ssh/authorized_keys"
    else
        : > "$DATA_DIR/ssh/authorized_keys"
    fi
    echo "Criado $DATA_DIR/ssh/authorized_keys — colar a chave pública SSH"
fi

# db/ e logs/ do utilizador que corre o portal (SSH_UID); o entrypoint também o faz no arranque
if [ "$(id -u)" -eq 0 ]; then
    chown -R "$SSH_UID:$SSH_UID" "$DATA_DIR/db" "$DATA_DIR/logs"
else
    sudo chown -R "$SSH_UID:$SSH_UID" "$DATA_DIR/db" "$DATA_DIR/logs" || \
        echo "AVISO: não foi possível mudar o dono de db/ e logs/ (o entrypoint tenta no arranque)"
fi

ls -la "$DATA_DIR"
echo "Pronto. Seguinte: docker-compose up -d --build"
