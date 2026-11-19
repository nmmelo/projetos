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

SUB_PADRAO="docker/portal-oracle"
PASTA="/opt/oracle-portal"
REPO="" BRANCH="" SUB="" SO_CODIGO=0 SIM=0

ajuda() {
    cat <<EOF
Uso: $(basename "$0") [opções]

Instala (1.ª vez) ou atualiza (seguintes) o portal-oracle em Docker na PASTA.

  -d PASTA     Pasta de destino (por omissão $PASTA)
  -r URL       Repositório Git (HTTPS ou SSH). Obrigatório na 1.ª vez;
               depois fica guardado em PASTA/.origem
  -b BRANCH    Branch (por omissão a guardada, ou 'main')
  -p SUBPASTA  Pasta do portal dentro do repositório (por omissão $SUB_PADRAO)
  -c           Só atualiza o código (não mexe no Docker)
  -y           Não pede confirmação
  -v           Mostra a versão instalada e a origem, e sai
  -h           Esta ajuda

Exemplos:
  sudo $(basename "$0") -d /opt/oracle-portal -r https://github.com/<conta>/<repositorio>.git
  sudo $(basename "$0") -d /opt/oracle-portal          # atualizar para a última versão
  sudo $(basename "$0") -d /opt/oracle-portal -b teste # mudar de branch

Na PASTA: o código é substituído pela versão do repositório; data/ (BD, fernet.key,
backups, homes) e .env ficam como estão. Não guarde outros ficheiros seus na PASTA:
os que não vêm do repositório são apagados.
EOF
}

erro() { echo "ERRO: $*" >&2; exit 1; }
info() { echo "== $*"; }

VER=0
while getopts "d:r:b:p:cyvh" o; do
    case "$o" in
        d) PASTA="$OPTARG" ;;
        r) REPO="$OPTARG" ;;
        b) BRANCH="$OPTARG" ;;
        p) SUB="$OPTARG" ;;
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
# substitui o codigo: apaga tudo menos data/, .env, .origem e .versao, e copia a versao nova
find "$PASTA" -mindepth 1 -maxdepth 1 ! -name data ! -name .env ! -name .origem ! -name .versao -exec rm -rf {} +
cp -a "$TMP/repo/$SUB/." "$PASTA/"
printf 'REPO=%s\nBRANCH=%s\nSUB=%s\n' "$REPO" "$BRANCH" "$SUB" > "$PASTA/.origem"
chmod 600 "$PASTA/.origem"
echo "$VERSAO" > "$PASTA/.versao"
mkdir -p "$PASTA/data/ssh/users" "$PASTA/data/home"
[ -f "$PASTA/.env" ] || { cp "$PASTA/.env.example" "$PASTA/.env"; info ".env criado a partir do .env.example (reveja-o)"; }

if [ -n "$ANTES" ] && [ "$ANTES" = "${VERSAO%% *}" ]; then info "já estava na última versão: $VERSAO"
else info "versão: $VERSAO${ANTES:+ (antes: $ANTES)}"; fi

[ "$SO_CODIGO" = 1 ] && { info "código atualizado em $PASTA (Docker não foi alterado)"; exit 0; }

cd "$PASTA"
if [ "$PRIMEIRA" = 1 ]; then
    info "a construir e arrancar o contentor"
    "${DC[@]}" up -d --build
    info "password do administrador (admin)"
    "${DC[@]}" exec -u portal portal python manage.py set-admin-password || \
        echo "Defina-a depois com: cd $PASTA && ${DC[*]} exec -u portal portal python manage.py set-admin-password"
else
    info "a aplicar (reconstrói a imagem só se o Dockerfile mudou) e a reiniciar"
    "${DC[@]}" up -d --build
    "${DC[@]}" restart
fi
info "pronto: http://$(hostname -f 2>/dev/null || hostname):8080  - no browser, faça Ctrl+F5"
