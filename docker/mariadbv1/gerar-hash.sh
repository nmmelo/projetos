#!/usr/bin/env bash
# Gera hashes mysql_native_password e atualiza o .env da MariaDB.
# Uso: ./gerar-hash.sh [ficheiro .env]   (por omissão: .env na pasta do script)
set -euo pipefail

ENV_FILE="${1:-$(dirname "$0")/.env}"

hash_pw() {
  if command -v openssl >/dev/null 2>&1; then
    printf '%s' "$1" | openssl sha1 -binary | openssl sha1 | awk '{print "*" toupper($NF)}'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import hashlib,sys;print("*"+hashlib.sha1(hashlib.sha1(sys.argv[1].encode()).digest()).hexdigest().upper())' "$1"
  else
    echo "Erro: é preciso openssl ou python3." >&2; exit 1
  fi
}

ask_pw() {   # $1 = descrição
  local p1 p2
  while true; do
    read -rsp "Password $1: " p1; echo >&2
    read -rsp "Confirmar password $1: " p2; echo >&2
    if [[ -z "$p1" ]]; then echo "  A password não pode estar vazia." >&2
    elif [[ "$p1" != "$p2" ]]; then echo "  As passwords não coincidem." >&2
    elif (( ${#p1} < 12 )); then echo "  Use pelo menos 12 caracteres." >&2
    else printf '%s' "$p1"; return; fi
  done
}

set_var() {  # $1 = chave, $2 = valor
  if grep -q "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}

touch "$ENV_FILE"
[[ -s "$ENV_FILE" ]] && cp "$ENV_FILE" "$ENV_FILE.bak"

read -rp "Utilizador admin [admin]: " ADMIN_USER;  ADMIN_USER="${ADMIN_USER:-admin}"
read -rp "IP de acesso do admin [192.0.2.25]: " ADMIN_HOST; ADMIN_HOST="${ADMIN_HOST:-192.0.2.25}"

ROOT_HASH=$(hash_pw "$(ask_pw root)")
ADMIN_HASH=$(hash_pw "$(ask_pw "$ADMIN_USER")")

set_var MARIADB_ROOT_PASSWORD_HASH  "$ROOT_HASH"
set_var MARIADB_ADMIN_USER          "$ADMIN_USER"
set_var MARIADB_ADMIN_PASSWORD_HASH "$ADMIN_HASH"
set_var MARIADB_ADMIN_HOST          "$ADMIN_HOST"
chmod 600 "$ENV_FILE"

echo
echo "✔ $ENV_FILE atualizado (backup: $ENV_FILE.bak)"
grep -E '^MARIADB_' "$ENV_FILE"
