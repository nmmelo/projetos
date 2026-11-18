#!/bin/bash
# preparar.sh — cria as pastas dos mounts antes do 1.º arranque (o Docker do Synology não as cria).
# Uso: ./preparar.sh   (na pasta do projeto, com o .env preenchido)
#      DATA_DIR=/volume1/docker/ansible-semaphore SSH_UID=1026 ./preparar.sh   (sem .env, ex. stack do Portainer)
set -euo pipefail
cd "$(dirname "$0")"

valor() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/' || true; }

DATA_DIR=${DATA_DIR:-$(valor DATA_DIR)};           DATA_DIR=${DATA_DIR:-.}
PLAYBOOKS_DIR=${PLAYBOOKS_DIR:-$(valor PLAYBOOKS_DIR)}; PLAYBOOKS_DIR=${PLAYBOOKS_DIR:-$DATA_DIR/workspace}
SSH_UID=${SSH_UID:-$(valor SSH_UID)};               SSH_UID=${SSH_UID:-1026}
SSH_GID=${SSH_GID:-$(valor SSH_GID)};               SSH_GID=${SSH_GID:-100}
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"

echo "DATA_DIR      = $DATA_DIR"
echo "PLAYBOOKS_DIR = $PLAYBOOKS_DIR"

mkdir -p "$DATA_DIR/secrets" "$DATA_DIR/hostkeys" \
         "$DATA_DIR/semaphore/data" "$DATA_DIR/semaphore/config" "$DATA_DIR/semaphore/tmp" \
         "$PLAYBOOKS_DIR"

# ansible.cfg de exemplo, se ainda não existir
if [ ! -f "$DATA_DIR/secrets/ansible.cfg" ] && [ -f secrets/ansible.cfg.example ]; then
    cp secrets/ansible.cfg.example "$DATA_DIR/secrets/ansible.cfg"
    echo "Criado $DATA_DIR/secrets/ansible.cfg — rever remote_user e private_key_file"
fi

# secrets: só o dono; playbooks: do utilizador SSH; Semaphore corre com UID 1001
$SUDO chown -R "$SSH_UID:$SSH_GID" "$DATA_DIR/secrets"
$SUDO chmod 700 "$DATA_DIR/secrets"
$SUDO find "$DATA_DIR/secrets" -type f -exec chmod 600 {} +
$SUDO chown "$SSH_UID:$SSH_GID" "$PLAYBOOKS_DIR"
$SUDO chown -R 1001:0 "$DATA_DIR/semaphore"

ls -la "$DATA_DIR" "$DATA_DIR/secrets"
echo
echo "Falta copiar para $DATA_DIR/secrets: a chave privada (ANSIBLE_KEY_FILE) e, se usar vault, .vault_pass"
echo "Seguinte: docker compose up -d --build   (ou deploy da stack no Portainer)"
