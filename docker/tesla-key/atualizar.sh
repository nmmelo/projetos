#!/bin/bash
# atualizar.sh — atualiza o tesla-key a partir do repositório, sem tocar na configuração nem nos dados.
#
# Uso (na pasta do projeto, no NAS):
#   ./atualizar.sh              descarrega, mostra o que mudou, faz backup e rebuild
#   ./atualizar.sh --simular    só mostra o que mudaria
#   ./atualizar.sh --sem-build  atualiza os ficheiros sem rebuild
#
# Origem: UPDATE_URL no .env (URL "raw" da pasta tesla-key no repositório), ex.:
#   UPDATE_URL=https://raw.githubusercontent.com/<utilizador>/<repositorio>/main/docker/tesla-key
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

SIMULAR=0; BUILD=1
for a in "$@"; do
    case "$a" in
        --simular)   SIMULAR=1 ;;
        --sem-build) BUILD=0 ;;
        -h|--help)   sed -n '2,10p' "$0"; exit 0 ;;
        *)           echo "opção desconhecida: $a"; exit 1 ;;
    esac
done

# Nunca substituídos, mesmo que apareçam na lista remota
PROTEGIDOS='^(\.env|ssh/.*|tesla/tesla\.env|tesla/keys/.*|tesla/tokens\.json|tesla/gateway\.db|tesla/imagens/.*|hostkeys/.*|html/.*)$'

die()  { echo "ERRO: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[ -f docker-compose.yml ] || die "corra o script na pasta do tesla-key"
URL="${UPDATE_URL:-}"
ler_env() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]#.*//' | tr -d '"'"'" | xargs || true; }
[ -z "$URL" ] && URL=$(ler_env UPDATE_URL)
[ -n "$URL" ] || die "defina UPDATE_URL no .env (ver .env.example)"
URL="${URL%/}"
command -v curl >/dev/null || die "falta o curl"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

info "a obter a lista de ficheiros de $URL"
curl -fsSL "$URL/atualizar.lista" -o "$TMP/lista" || die "não consegui obter atualizar.lista (UPDATE_URL certo?)"

NOVOS=(); ALTERADOS=(); IGUAIS=0
while IFS= read -r f; do
    f="${f%%#*}"; f="$(echo "$f" | xargs)"
    [ -z "$f" ] && continue
    if [[ "$f" =~ $PROTEGIDOS ]] || [[ "$f" == *..* ]] || [[ "$f" == /* ]]; then
        echo "  ignorado (protegido): $f"; continue
    fi
    mkdir -p "$TMP/novo/$(dirname "$f")"
    curl -fsSL "$URL/$f" -o "$TMP/novo/$f" || die "falhou o download de $f"
    if [ ! -f "$f" ]; then
        NOVOS+=("$f")
    elif ! cmp -s "$f" "$TMP/novo/$f"; then
        ALTERADOS+=("$f")
    else
        IGUAIS=$((IGUAIS + 1))
    fi
done < "$TMP/lista"

echo
printf '%-10s %s\n' "Novos:" "${#NOVOS[@]}"; for f in "${NOVOS[@]}"; do echo "   + $f"; done
printf '%-10s %s\n' "Alterados:" "${#ALTERADOS[@]}"; for f in "${ALTERADOS[@]}"; do
    echo "   ~ $f ($(diff "$f" "$TMP/novo/$f" | grep -c '^[<>]') linhas)"; done
printf '%-10s %s\n' "Iguais:" "$IGUAIS"

# Variáveis novas nos modelos que faltam na configuração real
avisar_vars() {
    local modelo="$1" real="$2"
    [ -f "$modelo" ] && [ -f "$real" ] || return 0
    local falta
    falta=$(comm -23 <(grep -oE '^[A-Z_]+=' "$modelo" | sort -u) <(grep -oE '^[A-Z_]+=' "$real" | sort -u) | tr -d '=' | xargs)
    [ -n "$falta" ] && echo "AVISO: variáveis de $modelo que não estão em $real (ficam com o valor por omissão): $falta"
    return 0
}

if [ ${#NOVOS[@]} -eq 0 ] && [ ${#ALTERADOS[@]} -eq 0 ]; then
    echo; info "já está na versão mais recente"
    avisar_vars .env.example .env; avisar_vars tesla/tesla.env.example tesla/tesla.env
    exit 0
fi
[ "$SIMULAR" = 1 ] && { echo; info "simulação: nada foi alterado"; exit 0; }

BK=".backup/$(date +%Y%m%d_%H%M%S)"
for f in "${ALTERADOS[@]}"; do mkdir -p "$BK/$(dirname "$f")"; cp -p "$f" "$BK/$f"; done
[ ${#ALTERADOS[@]} -gt 0 ] && info "backup dos ficheiros alterados em $BK"

PROPRIO=0
for f in "${NOVOS[@]}" "${ALTERADOS[@]}"; do
    if [ "$f" = "atualizar.sh" ]; then PROPRIO=1; continue; fi   # substituído no fim
    mkdir -p "$(dirname "$f")"; cp "$TMP/novo/$f" "$f"
done
chmod +x tesla-setup.sh entrypoint.sh 2>/dev/null || true
info "ficheiros atualizados"

echo
avisar_vars .env.example .env
avisar_vars tesla/tesla.env.example tesla/tesla.env

if [ "$BUILD" = 1 ]; then
    SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    info "rebuild do contentor"
    $SUDO docker compose up -d --build --force-recreate
    sleep 3
    CN=$(ler_env CONTAINER_NAME); CN="${CN:-tesla-key}"
    if $SUDO docker exec "$CN" curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        info "gateway OK (/api/health)"
    else
        echo "AVISO: o gateway ainda não responde; ver: $SUDO docker logs $CN --tail 30"
    fi
else
    info "sem rebuild: correr  sudo docker compose up -d --build --force-recreate"
fi

if [ "$PROPRIO" = 1 ]; then
    cp "$TMP/novo/atualizar.sh" atualizar.sh.novo && chmod +x atualizar.sh.novo && mv atualizar.sh.novo atualizar.sh
    info "atualizar.sh também foi atualizado"
fi
