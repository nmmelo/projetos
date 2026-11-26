#!/usr/bin/env bash
# =============================================================================
# portal-docker.sh - instala ou atualiza o portal-oracle em Docker, numa pasta
#
# Descarrega so a pasta docker/portal-oracle do repositorio (sparse checkout,
# sem historico; so precisa do git) para uma pasta temporaria e copia-a para a pasta de destino.
# data/ e .env nunca sao alterados. Na 1.a vez cria o .env e pede a password
# do admin; nas seguintes atualiza o codigo e reinicia o contentor.
#
# A origem (repositorio, branch, pasta) fica guardada em <PASTA>/.origem, por
# isso as atualizacoes seguintes so precisam da pasta.
# =============================================================================
set -euo pipefail
# servidores com umask restrita (027/077) criariam o codigo so legivel pelo root: o Nginx (www-data)
# e o Gunicorn (portal) dentro do contentor nao o conseguiriam ler -> 403 Forbidden
umask 022

SUB_PADRAO="docker/portal-oracle"
PASTA="/opt/oracle-portal"
REPO="" BRANCH="" SUB="" SO_CODIGO=0 SIM=0 P_HTTP="" P_SSH=""

ajuda() {
    cat <<EOF
Uso: $(basename "$0") [opções]

Instala (1.ª vez) ou atualiza (seguintes) o portal-oracle em Docker na PASTA.

  -d PASTA     Pasta de destino (por omissão $PASTA)
  -r URL       Repositório Git (HTTPS ou SSH). Obrigatório na 1.ª vez;
               depois fica guardado em PASTA/.origem
  -b BRANCH    Branch (por omissão a guardada, ou 'main')
  -p SUBPASTA  Pasta do portal dentro do repositório (por omissão $SUB_PADRAO)
  -H PORTA     Porta HTTP no anfitrião (guardada no .env, PORTAL_HTTP_PORT; omissão 8080)
  -S PORTA     Porta SSH no anfitrião (guardada no .env, PORTAL_SSH_PORT; omissão 2222)
  -c           Só atualiza o código (não mexe no Docker)
  -y           Não pede confirmação
  -v           Mostra a versão instalada e a origem, e sai
  -h           Esta ajuda

Exemplos:
  sudo $(basename "$0") -d /opt/oracle-portal -r https://github.com/<conta>/<repositorio>.git
  sudo $(basename "$0") -d /opt/oracle-portal          # atualizar para a última versão
  sudo $(basename "$0") -d /opt/oracle-portal -b teste # mudar de branch
  sudo $(basename "$0") -d /opt/oracle-portal -H 8090 -S 2223   # mudar as portas

Na PASTA: o código é substituído pela versão do repositório; data/ (BD, fernet.key,
backups, homes), .env e docker-compose.override.yml ficam como estão. As portas
e outras alterações locais vão para o .env (ou para o docker-compose.override.yml),
nunca para o docker-compose.yml. Não guarde outros ficheiros seus na PASTA:
os que não vêm do repositório são apagados.
EOF
}

erro() { echo "ERRO: $*" >&2; exit 1; }
info() { echo "== $*"; }

VER=0
while getopts "d:r:b:p:H:S:cyvh" o; do
    case "$o" in
        d) PASTA="$OPTARG" ;;
        r) REPO="$OPTARG" ;;
        b) BRANCH="$OPTARG" ;;
        p) SUB="$OPTARG" ;;
        H) P_HTTP="$OPTARG" ;;
        S) P_SSH="$OPTARG" ;;
        c) SO_CODIGO=1 ;;
        y) SIM=1 ;;
        v) VER=1 ;;
        h) ajuda; exit 0 ;;
        *) ajuda >&2; exit 2 ;;
    esac
done
[ -n "$PASTA" ] || PASTA="/"
PASTA="$(realpath -m "$PASTA")"

# origem guardada
if [ -f "$PASTA/.origem" ]; then
    G_REPO="" G_BRANCH="" G_SUB=""
    while IFS='=' read -r k v; do
        case "$k" in REPO) G_REPO="$v" ;; BRANCH) G_BRANCH="$v" ;; SUB) G_SUB="$v" ;; esac
    done < "$PASTA/.origem"
    REPO="${REPO:-$G_REPO}"; BRANCH="${BRANCH:-$G_BRANCH}"; SUB="${SUB:-$G_SUB}"
fi
BRANCH="${BRANCH:-main}"; SUB="${SUB:-$SUB_PADRAO}"

if [ "$VER" = 1 ]; then
    echo "Pasta:   $PASTA"
    echo "Versão:  $(cat "$PASTA/.versao" 2>/dev/null || echo 'desconhecida')"
    echo "Origem:  ${REPO:-?} (branch $BRANCH, pasta $SUB)"
    exit 0
fi

for v in "$P_HTTP" "$P_SSH"; do
    [ -z "$v" ] || { [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -ge 1 ] && [ "$v" -le 65535 ]; } || erro "porta inválida: $v"
done
[ -n "$REPO" ] || erro "indique o repositório com -r URL (só é preciso na 1.ª vez). Veja --help: $(basename "$0") -h"
command -v git >/dev/null || erro "falta o git (ex. apt install -y git)"
case "$PASTA" in
    /|/opt|/home|/root|/usr|/usr/*|/etc|/etc/*|/var|/srv|/tmp|/bin|/sbin|/lib*|/boot|/dev|/proc|/sys|/run)
        erro "pasta de destino inválida: $PASTA (use uma pasta própria, ex. /opt/oracle-portal)" ;;
esac
if cut -d: -f6 /etc/passwd | grep -qxF "$PASTA"; then
    erro "pasta de destino inválida: $PASTA é a home de um utilizador (use uma pasta própria, ex. /opt/oracle-portal)"
fi
if [ "$SO_CODIGO" = 0 ]; then
    if docker compose version >/dev/null 2>&1; then DC=(docker compose)
    elif command -v docker-compose >/dev/null; then DC=(docker-compose)
    else erro "falta o Docker Compose (docker compose ou docker-compose); use -c para só descarregar o código"; fi
fi

PRIMEIRA=0; [ -f "$PASTA/docker-compose.yml" ] || PRIMEIRA=1
if [ "$PRIMEIRA" = 0 ] && [ ! -f "$PASTA/.origem" ] && [ -n "$(ls -A "$PASTA" 2>/dev/null)" ] && [ "$SIM" = 0 ]; then
    echo "A pasta $PASTA já tem ficheiros e não foi criada por este script."
    read -rp "Substituir o código (data/ e .env ficam)? [s/N] " r; [[ "$r" =~ ^[sS]$ ]] || exit 1
elif [ "$PRIMEIRA" = 1 ] && [ -n "$(ls -A "$PASTA" 2>/dev/null)" ] && [ "$SIM" = 0 ]; then
    echo "A pasta $PASTA não está vazia: os ficheiros que não vêm do repositório são apagados (exceto data/ e .env)."
    read -rp "Continuar? [s/N] " r; [[ "$r" =~ ^[sS]$ ]] || exit 1
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
info "a descarregar $SUB (branch $BRANCH)"
git clone -q --depth 1 --filter=blob:none --sparse -b "$BRANCH" "$REPO" "$TMP/repo" \
    || erro "não foi possível clonar: confirme o URL, o branch '$BRANCH' e o acesso (repositório privado: token no HTTPS ou chave SSH)"
git -C "$TMP/repo" sparse-checkout set "$SUB"
[ -f "$TMP/repo/$SUB/docker-compose.yml" ] || erro "a pasta '$SUB' não existe no branch '$BRANCH' ou não é o portal"
VERSAO="$(git -C "$TMP/repo" log -1 --format='%h %ad %s' --date=short)"
ANTES="$(cut -d' ' -f1 "$PASTA/.versao" 2>/dev/null || true)"

mkdir -p "$PASTA"
# portas alteradas a mao num docker-compose.yml antigo (antes das variaveis no .env): passam para o .env
OLD_HTTP="" OLD_SSH=""
if [ -f "$PASTA/docker-compose.yml" ] && ! grep -q 'PORTAL_HTTP_PORT' "$PASTA/docker-compose.yml"; then
    OLD_HTTP="$(sed -nE 's/^[[:space:]]*-[[:space:]]*"?([0-9.]+:)?([0-9]+):80"?.*/\2/p' "$PASTA/docker-compose.yml" | head -1)"
    OLD_SSH="$(sed -nE 's/^[[:space:]]*-[[:space:]]*"?([0-9.]+:)?([0-9]+):22"?.*/\2/p' "$PASTA/docker-compose.yml" | head -1)"
fi
# substitui o codigo: apaga tudo menos data/, .env, .origem e .versao, e copia a versao nova
find "$PASTA" -mindepth 1 -maxdepth 1 ! -name data ! -name .env ! -name .origem ! -name .versao \
    ! -name docker-compose.override.yml ! -name 'docker-compose.override.yaml' -exec rm -rf {} +
cp -a "$TMP/repo/$SUB/." "$PASTA/"
# o codigo tem de ser legivel por todos dentro do contentor (www-data e portal); data/ e .env nao
chmod 755 "$PASTA"
find "$PASTA" -mindepth 1 -maxdepth 1 ! -name data ! -name .env ! -name .origem \
    -exec chmod -R u+rwX,go+rX,go-w {} +
printf 'REPO=%s\nBRANCH=%s\nSUB=%s\n' "$REPO" "$BRANCH" "$SUB" > "$PASTA/.origem"
chmod 600 "$PASTA/.origem"
echo "$VERSAO" > "$PASTA/.versao"
mkdir -p "$PASTA/data/ssh/users" "$PASTA/data/home"
[ -f "$PASTA/.env" ] || { cp "$PASTA/.env.example" "$PASTA/.env"; info ".env criado a partir do .env.example (reveja-o)"; }

# variavel no .env: altera a linha se existir, senao acrescenta
env_set() {
    if grep -q "^$1=" "$PASTA/.env"; then sed -i "s|^$1=.*|$1=$2|" "$PASTA/.env"; else printf '%s=%s\n' "$1" "$2" >> "$PASTA/.env"; fi
}
env_get() { sed -n "s/^$1=//p" "$PASTA/.env" | tail -1; }
if [ -z "$(env_get PORTAL_HTTP_PORT)" ] && [ -n "$OLD_HTTP" ] && [ -z "$P_HTTP" ]; then P_HTTP="$OLD_HTTP"; info "porta HTTP $OLD_HTTP do docker-compose.yml antigo passou para o .env"; fi
if [ -z "$(env_get PORTAL_SSH_PORT)" ] && [ -n "$OLD_SSH" ] && [ -z "$P_SSH" ]; then P_SSH="$OLD_SSH"; info "porta SSH $OLD_SSH do docker-compose.yml antigo passou para o .env"; fi
[ -z "$P_HTTP" ] || env_set PORTAL_HTTP_PORT "$P_HTTP"
[ -z "$P_SSH" ] || env_set PORTAL_SSH_PORT "$P_SSH"
# .env de versoes anteriores (sem as portas): acrescenta os valores por omissao, para ficarem visiveis
[ -n "$(env_get PORTAL_HTTP_PORT)" ] || env_set PORTAL_HTTP_PORT 8080
[ -n "$(env_get PORTAL_SSH_PORT)" ] || env_set PORTAL_SSH_PORT 2222
# limpa as portas (CR do Windows, espacos, aspas, comentarios no fim) e valida
sed -i 's/\r$//' "$PASTA/.env"
for k in PORTAL_HTTP_PORT PORTAL_SSH_PORT; do
    v="$(env_get "$k" | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]"'"'"']+//; s/[[:space:]"'"'"']+$//')"
    [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -ge 1 ] && [ "$v" -le 65535 ] \
        || erro "$k='$(env_get "$k")' no $PASTA/.env não é uma porta (use só o número, ex. $k=$([ "$k" = PORTAL_SSH_PORT ] && echo 2223 || echo 8090))"
    env_set "$k" "$v"
done
# so uma linha por variavel (a ultima vale)
for k in PORTAL_HTTP_PORT PORTAL_SSH_PORT; do
    v="$(env_get "$k")"; sed -i "/^$k=/d" "$PASTA/.env"; printf '%s=%s\n' "$k" "$v" >> "$PASTA/.env"
done
HTTP="$(env_get PORTAL_HTTP_PORT)"; SSHP="$(env_get PORTAL_SSH_PORT)"
info "portas: HTTP $HTTP, SSH $SSHP (no .env)"

if [ -n "$ANTES" ] && [ "$ANTES" = "${VERSAO%% *}" ]; then info "já estava na última versão: $VERSAO"
else info "versão: $VERSAO${ANTES:+ (antes: $ANTES)}"; fi

[ "$SO_CODIGO" = 1 ] && { info "código atualizado em $PASTA (Docker não foi alterado)"; exit 0; }

cd "$PASTA"
"${DC[@]}" config -q || erro "o Docker Compose não aceita a configuração: reveja o $PASTA/.env (PORTAL_HTTP_PORT, PORTAL_SSH_PORT) e o docker-compose.override.yml"
CONT="$(env_get PORTAL_CONTAINER)"; CONT="${CONT:-oracle-portal}"
# outro contentor com o mesmo nome (ex. outra instalacao noutra pasta) impede o arranque
OUTRO="$(docker ps -a --filter "name=^/${CONT}\$" --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | head -1)"
if [ -n "$OUTRO" ] && [ "$(realpath -m "$OUTRO")" != "$PASTA" ]; then
    erro "já existe um contentor '$CONT' de outra pasta ($OUTRO). Pare-o (cd $OUTRO && ${DC[*]} down) ou dê outro nome a este: PORTAL_CONTAINER=<nome> no $PASTA/.env"
fi

# portas do anfitriao ja ocupadas por outro programa/contentor (ex. outro servico SSH na 2222): sem isto o
# Docker falha com "Bind for 0.0.0.0:2222 failed: port is already allocated". Propoe a proxima livre e grava-a no .env.
porta_ocupada() {   # 0 se a porta estiver a ser usada por algo que nao e este contentor
    local p=$1
    docker port "$CONT" 2>/dev/null | grep -qE ":$p\$" && return 1          # e deste contentor (atualizacao)
    if command -v ss >/dev/null 2>&1; then ss -Hltn "sport = :$p" 2>/dev/null | grep -q . && return 0
    else timeout 2 bash -c ">/dev/tcp/127.0.0.1/$p" 2>/dev/null && return 0; fi
    docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -v "^$CONT " | grep -qE "(0\.0\.0\.0|\[::\]|:::?):$p->" && return 0
    return 1
}
quem_usa() {
    local p=$1 c
    c="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -v "^$CONT " | grep -E ":$p->" | cut -d' ' -f1 | head -1)"
    [ -n "$c" ] && { echo "contentor $c"; return; }
    command -v ss >/dev/null 2>&1 && ss -Hltnp "sport = :$p" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -1 | sed 's/users:(("/processo /'
}
for k in PORTAL_HTTP_PORT PORTAL_SSH_PORT; do
    v="$(env_get "$k")"
    porta_ocupada "$v" || continue
    n=$((v + 1)); while porta_ocupada "$n" || [ "$n" = "$HTTP" ] || [ "$n" = "$SSHP" ]; do n=$((n + 1)); done
    u="$(quem_usa "$v")"
    echo "AVISO: a porta $v ($k) já está ocupada${u:+ por $u}; a próxima livre é a $n."
    if [ "$SIM" = 1 ] || [ ! -t 0 ]; then r=s; else read -r -p "   Usar a $n (fica no .env)? [S/n/outra porta] " r; r="${r:-s}"; fi
    case "$r" in
        [sS]*) n=$n ;;
        [0-9]*) n=$r; [[ "$n" =~ ^[0-9]+$ ]] && ! porta_ocupada "$n" || erro "a porta $n também não serve" ;;
        *) erro "escolha outra porta: $(basename "$0") -d $PASTA $([ "$k" = PORTAL_SSH_PORT ] && echo -S || echo -H) <porta>" ;;
    esac
    env_set "$k" "$n"
    info "$k=$n gravado no $PASTA/.env"
    [ "$k" = PORTAL_HTTP_PORT ] && HTTP=$n || SSHP=$n
done

# espera que o contentor fique a correr; senao mostra o log
a_correr() {
    local i st
    for i in $(seq 1 30); do
        st="$(docker inspect -f '{{.State.Status}}' "$CONT" 2>/dev/null || true)"
        [ "$st" = running ] && return 0
        sleep 1
    done
    echo "O contentor '$CONT' não está a correr (estado: ${st:-não existe}). Últimas linhas do log:" >&2
    "${DC[@]}" logs --tail 40 portal >&2 || true
    erro "veja o log acima; estado: cd $PASTA && ${DC[*]} ps"
}

if [ "$PRIMEIRA" = 1 ]; then
    info "a construir e arrancar o contentor $CONT"
    "${DC[@]}" up -d --build
    a_correr
    info "password do administrador (admin)"
    "${DC[@]}" exec -u portal portal python manage.py set-admin-password || \
        echo "Defina-a depois com: cd $PASTA && ${DC[*]} exec -u portal portal python manage.py set-admin-password"
else
    info "a aplicar (reconstrói a imagem só se o Dockerfile mudou) e a reiniciar"
    "${DC[@]}" up -d --build
    "${DC[@]}" restart
    a_correr
fi
H="$(hostname -f 2>/dev/null || hostname)"
info "pronto: http://$H:$HTTP  (SSH: ssh -p $SSHP <utilizador>@$H) - no browser, faça Ctrl+F5"
