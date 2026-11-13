#!/bin/bash
# ssh-utilizadores.sh — configura os utilizadores SSH do contentor em cada arranque (chamado pelo entrypoint).
#
# Utilizador principal (SSH_USER, criado na imagem):
#   chaves de /import/authorized_keys (ficheiro montado) + SSH_PUBKEY (variável)
# Utilizador extra opcional, ex. para outro portal de gestão:
#   SSH_EXTRA_USER    nome (ex.: adminssc)
#   SSH_EXTRA_PUBKEY  chave(s) pública(s); várias separadas por ";"
#   SSH_EXTRA_SUDO    1 (omissão) = sudo sem password; 0 = sem sudo
#   SSH_EXTRA_UID     UID (opcional)
#
# Contas sem password (só chave), com a password marcada como "*" (não bloqueada) para o sshd aceitar a chave.
set -euo pipefail

SSHD_CONFIG="${SSHD_CONFIG:-/etc/ssh/sshd_config}"
SUDOERS_DIR="${SUDOERS_DIR:-/etc/sudoers.d}"
FICHEIRO_CHAVES="${FICHEIRO_CHAVES:-/import/authorized_keys}"

aviso() { echo "AVISO (ssh): $*" >&2; }

# Escreve ~/.ssh/authorized_keys a partir de um ficheiro e/ou de uma variável (chaves separadas por ";" ou "|")
configurar_chaves() {
    local user="$1" ficheiro="$2" chaves_env="$3" home
    home=$(getent passwd "$user" | cut -d: -f6)
    [ -n "$home" ] || { aviso "utilizador $user sem home"; return 0; }
    mkdir -p "$home/.ssh"
    {
        [ -n "$ficheiro" ] && [ -f "$ficheiro" ] && cat "$ficheiro"
        printf '%s\n' "$chaves_env" | tr ';|' '\n\n'
    } | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
      | grep -E '^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-|sk-(ssh-ed25519|ecdsa-sha2))' \
      | awk '!visto[$0]++' > "$home/.ssh/authorized_keys" || true
    chmod 700 "$home/.ssh"
    chmod 600 "$home/.ssh/authorized_keys"
    chown -R "$user:$user" "$home/.ssh"
    local n; n=$(wc -l < "$home/.ssh/authorized_keys")
    [ "$n" -gt 0 ] || aviso "$user não tem nenhuma chave pública válida (não vai conseguir entrar)"
    echo "ssh: $user com $n chave(s)"
}

definir_sudo() {
    local user="$1" ativo="$2"
    if [ "$ativo" = "1" ]; then
        usermod -aG sudo "$user"
        printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$user" > "$SUDOERS_DIR/$user"
        chmod 440 "$SUDOERS_DIR/$user"
    else
        gpasswd -d "$user" sudo >/dev/null 2>&1 || true
        rm -f "$SUDOERS_DIR/$user"
    fi
}

# AllowUsers: uma única linha, antes de qualquer bloco Match
definir_allowusers() {
    local lista="$1" tmp
    tmp=$(mktemp)
    awk -v linha="AllowUsers $lista" '
        /^[[:space:]]*[Aa]llow[Uu]sers[[:space:]]/ { next }
        !feito && /^[[:space:]]*[Mm]atch[[:space:]]/ { print linha; feito=1 }
        { print }
        END { if (!feito) print linha }
    ' "$SSHD_CONFIG" > "$tmp"
    cat "$tmp" > "$SSHD_CONFIG"
    rm -f "$tmp"
}

# --- utilizador principal
: "${SSH_USER:?SSH_USER não definido}"
usermod -p '*' "$SSH_USER"
configurar_chaves "$SSH_USER" "$FICHEIRO_CHAVES" "${SSH_PUBKEY:-}"
PERMITIDOS="$SSH_USER"

# --- utilizador extra
if [ -n "${SSH_EXTRA_USER:-}" ]; then
    if ! [[ "$SSH_EXTRA_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
        aviso "SSH_EXTRA_USER inválido: '$SSH_EXTRA_USER' (minúsculas, números, - e _)"
    elif [ "$SSH_EXTRA_USER" = "$SSH_USER" ]; then
        aviso "SSH_EXTRA_USER igual a SSH_USER; use SSH_PUBKEY para acrescentar chaves ao utilizador principal"
    else
        if ! id -u "$SSH_EXTRA_USER" >/dev/null 2>&1; then
            useradd -m -s /bin/bash ${SSH_EXTRA_UID:+-u "$SSH_EXTRA_UID"} "$SSH_EXTRA_USER"
            echo "ssh: utilizador $SSH_EXTRA_USER criado"
        fi
        usermod -p '*' "$SSH_EXTRA_USER"
        definir_sudo "$SSH_EXTRA_USER" "${SSH_EXTRA_SUDO:-1}"
        configurar_chaves "$SSH_EXTRA_USER" "" "${SSH_EXTRA_PUBKEY:-}"
        PERMITIDOS="$PERMITIDOS $SSH_EXTRA_USER"
    fi
fi

definir_allowusers "$PERMITIDOS"
echo "ssh: AllowUsers $PERMITIDOS"
