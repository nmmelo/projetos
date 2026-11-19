#!/usr/bin/env bash
#
# instalar.sh - instalacao e configuracao do Portal Oracle num servidor (sem Docker).
#
#   sudo ./instalar.sh                    instalar, ou reconfigurar/atualizar uma instancia existente
#   sudo ./instalar.sh --desinstalar      remover uma instancia
#   ./instalar.sh --ajuda
#
# Pergunta tudo o que e preciso e trata do resto, sem ser preciso alterar ficheiros a mao:
#   - nome da instancia (servico systemd, site do Nginx, pasta e utilizador), ex. portal-oracle, nome-portal
#   - origem do codigo: a pasta onde esta este script, ou so a pasta do portal num repositorio Git
#     (clone parcial com sparse-checkout, sem descarregar o repositorio inteiro)
#   - base de dados do portal: SQLite, MariaDB ou PostgreSQL, local (instala e cria) ou remota
#     (gera o SQL e o script para correr no servidor remoto, incluindo pg_hba.conf, e testa a ligacao)
#   - Nginx: nome DNS, portas, HTTP, HTTPS com certificado proprio ou Let's Encrypt
#   - systemd, password do administrador, backup no cron e firewall
#
# Pode ser descarregado sozinho: se nao encontrar o codigo ao lado, oferece-se para o ir buscar ao Git.
#
set -Eeuo pipefail
shopt -u patsub_replacement 2>/dev/null || true   # bash 5.2: "&" na substituicao

# Repositorio por omissao para a opcao "Git" (vazio = perguntar). Ex.: https://github.com/<utilizador>/projetos.git
REPO_URL_PADRAO=""
REPO_BRANCH_PADRAO="main"
REPO_PASTA_PADRAO="docker/portal-oracle"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ESTADO_FICHEIRO=".instalacao"            # na pasta de instalacao: respostas anteriores (sem passwords)

# ============================================================================ apresentacao
if [[ -t 1 ]]; then
    C_AZ=$'\e[1;34m'; C_VD=$'\e[1;32m'; C_AM=$'\e[1;33m'; C_VM=$'\e[1;31m'; C_NG=$'\e[1m'; C_0=$'\e[0m'
else
    C_AZ=""; C_VD=""; C_AM=""; C_VM=""; C_NG=""; C_0=""
fi
titulo() { echo; echo "${C_AZ}== $*${C_0}"; }
info()   { echo "   $*"; }
ok()     { echo "   ${C_VD}OK${C_0} $*"; }
aviso()  { echo "   ${C_AM}AVISO${C_0} $*"; }
erro()   { echo "${C_VM}ERRO:${C_0} $*" >&2; exit 1; }
trap 'echo "${C_VM}ERRO na linha $LINENO:${C_0} $BASH_COMMAND" >&2' ERR

# perguntar VAR "texto" "valor por omissao" ["regex de validacao"]
perguntar() {
    local __v=$1 __t=$2 __d=${3:-} __re=${4:-} __r
    while :; do
        if [[ -n $__d ]]; then read -r -p "   $__t [$__d]: " __r; else read -r -p "   $__t: " __r; fi
        __r=${__r:-$__d}
        if [[ -z $__r && -z $__re ]]; then :;
        elif [[ -n $__re && ! $__r =~ $__re ]]; then echo "   Valor invalido."; continue; fi
        printf -v "$__v" '%s' "$__r"; return 0
    done
}

# sim_nao "texto" s|n  -> 0 = sim
sim_nao() {
    local __r __d=${2:-n}
    while :; do
        read -r -p "   $1 [$([[ $__d == s ]] && echo S/n || echo s/N)]: " __r
        __r=${__r:-$__d}
        case ${__r,,} in s|sim|y|yes) return 0 ;; n|nao|não|no) return 1 ;; esac
    done
}

# escolher VAR "pergunta" chave_por_omissao "chave|texto" "chave|texto" ...
escolher() {
    local __v=$1 __t=$2 __d=$3; shift 3
    local -a chaves=() ; local i=1 op def_n=1 __r
    echo "   $__t"
    for op in "$@"; do
        chaves+=("${op%%|*}")
        [[ ${op%%|*} == "$__d" ]] && def_n=$i
        printf "     %d) %s\n" "$i" "${op#*|}"
        i=$((i + 1))
    done
    while :; do
        read -r -p "   Opcao [$def_n]: " __r
        __r=${__r:-$def_n}
        if [[ $__r =~ ^[0-9]+$ ]] && (( __r >= 1 && __r <= ${#chaves[@]} )); then
            printf -v "$__v" '%s' "${chaves[__r - 1]}"; return 0
        fi
        echo "   Opcao invalida."
    done
}

ler_password() {   # ler_password VAR "texto"  (pede duas vezes)
    local __v=$1 __p1 __p2
    while :; do
        read -r -s -p "   $2: " __p1; echo
        read -r -s -p "   Repetir: " __p2; echo
        [[ -n $__p1 && $__p1 == "$__p2" ]] && { printf -v "$__v" '%s' "$__p1"; return 0; }
        echo "   As passwords nao coincidem (ou estao vazias)."
    done
}

gerar_password() { openssl rand -base64 33 | tr -dc 'A-Za-z0-9' | cut -c1-24; }

uso() { sed -n '3,19p' "$0" | sed 's/^# \{0,1\}//'; }

# ============================================================================ utilitarios
render() {   # render origem destino  (substitui @CHAVE@ pelas variaveis T_CHAVE)
    local src=$1 dst=$2 v conteudo
    conteudo=$(<"$src")
    for v in NOME DIR UTILIZADOR BIND SERVER_NAME HTTP_PORT HTTPS_PORT HTTPS_URL SSL_CERT SSL_KEY \
             BD PASSWORD CLIENTE; do
        local nv="T_$v"
        conteudo=${conteudo//@$v@/${!nv:-}}
    done
    printf '%s\n' "$conteudo" > "$dst"
}

porta_livre() { ! ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; }

proxima_porta_livre() {
    local p=$1
    while ! porta_livre "$p"; do p=$((p + 1)); done
    echo "$p"
}

ip_de_saida() {   # IP local usado para chegar a um host
    local alvo ip
    alvo=$(getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}')
    [[ -n $alvo ]] || return 1
    ip=$(ip -4 route get "$alvo" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
    [[ -n $ip ]] && echo "$ip"
}

set_env() {   # set_env CHAVE VALOR  (no .env da instalacao)
    local f="$DIR/.env"
    if grep -q "^$1=" "$f"; then sed -i "s|^$1=.*|$1=$2|" "$f"; else echo "$1=$2" >> "$f"; fi
}

como_portal() { runuser -u "$UTIL" -- "$@"; }

manage() { ( cd "$DIR" && runuser -u "$UTIL" -w PORTAL_DB_PASSWORD -- "$DIR/.venv/bin/python" manage.py "$@" ); }

# Extrai so uma pasta de um repositorio Git (mesma logica do projeto linux/geral/extrair_pasta_git):
# clone sem blobs e com profundidade 1 + sparse-checkout, para nao descarregar o repositorio inteiro.
extrair_pasta_git() {   # extrair_pasta_git URL BRANCH PASTA DESTINO
    local url=$1 branch=$2 pasta=${3#/} destino=$4 tmp
    pasta=${pasta%/}
    tmp=$(mktemp -d)
    info "A obter '$pasta' (branch $branch) de $url ..."
    if ! git clone --quiet --filter=blob:none --no-checkout --depth 1 --branch "$branch" "$url" "$tmp/repo"; then
        rm -rf "$tmp"; return 1
    fi
    git -C "$tmp/repo" sparse-checkout init --cone
    git -C "$tmp/repo" sparse-checkout set "$pasta"
    git -C "$tmp/repo" checkout --quiet "$branch"
    if [[ ! -d $tmp/repo/$pasta ]]; then
        rm -rf "$tmp"; aviso "a pasta '$pasta' nao existe na branch '$branch'."; return 1
    fi
    mkdir -p "$destino"
    cp -a "$tmp/repo/$pasta/." "$destino/"
    rm -rf "$tmp"
}

copiar_codigo() {   # copiar_codigo ORIGEM  -> $DIR (nunca toca em data/, .env, .venv)
    [[ $(realpath "$1") == $(realpath "$DIR") ]] && return 0
    tar -C "$1" --exclude=./data --exclude=./.env --exclude=./.venv --exclude=./.git \
        --exclude="./$ESTADO_FICHEIRO" --exclude='__pycache__' -cf - . | tar -C "$DIR" -xf -
}

tem_codigo() { [[ -f $1/wsgi.py && -f $1/manage.py && -d $1/app && -d $1/deploy ]]; }

# ============================================================================ sistema
detetar_sistema() {
    if command -v apt-get >/dev/null; then
        PKG=apt
    elif command -v dnf >/dev/null; then
        PKG=dnf
    else
        erro "sistema nao suportado (precisa de apt ou dnf)."
    fi
}

instalar_pacotes() {
    [[ $# -eq 0 ]] && return 0
    info "pacotes: $*"
    if [[ $PKG == apt ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" >/dev/null
    else
        dnf install -y -q "$@" >/dev/null
    fi
}

escolher_python() {
    local p
    for p in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
        if command -v "$p" >/dev/null && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            PY=$(command -v "$p"); return 0
        fi
    done
    return 1
}

# ============================================================================ desinstalar
desinstalar() {
    titulo "Desinstalar uma instancia"
    local nome unit dir util
    perguntar nome "Nome da instancia" "${1:-}" '^[a-z][a-z0-9-]{1,30}$'
    unit="/etc/systemd/system/$nome.service"
    dir=$(sed -n 's/^WorkingDirectory=//p' "$unit" 2>/dev/null || true)
    util=$(sed -n 's/^User=//p' "$unit" 2>/dev/null || true)
    [[ -n $dir ]] || perguntar dir "Pasta de instalacao" "/opt/$nome"
    info "Servico: $unit"
    info "Pasta:   $dir"
    sim_nao "Parar e remover o servico, o site do Nginx e o cron de '$nome'?" n || exit 0

    systemctl disable --now "$nome" 2>/dev/null || true
    rm -f "$unit" "/etc/cron.d/$nome"
    systemctl daemon-reload
    rm -f "/etc/nginx/sites-enabled/$nome" "/etc/nginx/sites-available/$nome" "/etc/nginx/conf.d/$nome.conf"
    if command -v nginx >/dev/null && nginx -t 2>/dev/null; then systemctl reload nginx || true; fi
    ok "servico, Nginx e cron removidos"

    if [[ -d $dir ]] && sim_nao "Apagar tambem a pasta $dir (codigo e dados)?" n; then
        local bk="/root/$nome-data-$(date +%Y%m%d%H%M%S).tar.gz"
        [[ -d $dir/data ]] && tar -C "$dir" -czf "$bk" data && ok "copia dos dados em $bk"
        rm -rf "$dir"; ok "pasta apagada"
    fi
    if [[ -n $util ]] && id "$util" >/dev/null 2>&1 && sim_nao "Apagar o utilizador de servico '$util'?" n; then
        userdel "$util" 2>/dev/null || true; ok "utilizador apagado"
    fi
    info "A base de dados do portal (MariaDB/PostgreSQL), se existir, nao foi alterada."
    [[ -d /etc/nginx/ssl/$nome ]] && info "Certificados copiados ficam em /etc/nginx/ssl/$nome (apague se ja nao forem precisos)."
    exit 0
}

# ============================================================================ inicio
case "${1:-}" in
    -h|--ajuda|--help) uso; exit 0 ;;
esac
[[ $EUID -eq 0 ]] || erro "corra como root: sudo $0"
# Se vier por pipe (curl | bash), as perguntas leem do terminal
if [[ ! -t 0 ]] && { : </dev/tty; } 2>/dev/null; then exec </dev/tty; fi
detetar_sistema
[[ ${1:-} == --desinstalar ]] && desinstalar "${2:-}"
[[ -z ${1:-} ]] || { uso; exit 2; }

echo "${C_NG}Portal Oracle - instalacao num servidor (sem Docker)${C_0}"
echo "Enter aceita o valor entre [ ]. Nada e alterado antes da confirmacao final."

# ---------------------------------------------------------------------------- 1. instancia
titulo "1. Instancia"
info "O nome identifica o servico systemd, o site do Nginx, a pasta e o utilizador de servico."
info "Permite ter varias instancias no mesmo servidor (ex. portal-oracle, nome-portal)."
perguntar NOME "Nome da instancia" "portal-oracle" '^[a-z][a-z0-9-]{1,30}$'

REINSTALAR=0
DIR_ANT=$(sed -n 's/^WorkingDirectory=//p' "/etc/systemd/system/$NOME.service" 2>/dev/null || true)
DIR=${DIR_ANT:-/opt/$NOME}
if [[ -f $DIR/$ESTADO_FICHEIRO ]]; then
    # shellcheck disable=SC1090
    source "$DIR/$ESTADO_FICHEIRO"
    REINSTALAR=1
    aviso "a instancia '$NOME' ja existe em $DIR: as respostas anteriores sao usadas como valores por omissao."
    info "Os dados (data/) e o .env sao mantidos."
fi

perguntar DIR "Pasta de instalacao" "$DIR" '^/[A-Za-z0-9._/-]+$'
DIR=${DIR%/}
perguntar UTIL "Utilizador de servico (sem login)" "${UTIL:-$NOME}" '^[a-z_][a-z0-9_-]{0,31}$'

# ---------------------------------------------------------------------------- 2. codigo
titulo "2. Codigo do portal"
OPCOES_COD=()
tem_codigo "$SCRIPT_DIR" && [[ $(realpath "$SCRIPT_DIR") != $(realpath -m "$DIR") ]] \
    && OPCOES_COD+=("local|Copiar da pasta deste script ($SCRIPT_DIR)")
OPCOES_COD+=("git|Obter so a pasta do portal de um repositorio Git (sparse-checkout)")
tem_codigo "$DIR" && OPCOES_COD+=("manter|Manter o codigo ja instalado em $DIR")
COD_DEF=${OPCOES_COD[0]%%|*}
[[ $REINSTALAR == 1 && -n ${GIT_URL:-} ]] && COD_DEF=git
escolher COD "Origem do codigo:" "$COD_DEF" "${OPCOES_COD[@]}"
if [[ $COD == git ]]; then
    perguntar GIT_URL "URL do repositorio (HTTPS ou SSH)" "${GIT_URL:-$REPO_URL_PADRAO}" '^.+$'
    perguntar GIT_BRANCH "Branch" "${GIT_BRANCH:-$REPO_BRANCH_PADRAO}" '^.+$'
    perguntar GIT_PASTA "Pasta do portal no repositorio" "${GIT_PASTA:-$REPO_PASTA_PADRAO}" '^.+$'
fi

# ---------------------------------------------------------------------------- 3. aplicacao
titulo "3. Aplicacao"
PORTA_ANT=${PORTA_GUNICORN:-}             # na reconfiguracao, a porta da propria instancia esta ocupada
PORTA_GUNICORN=${PORTA_ANT:-$(proxima_porta_livre 8000)}
while :; do
    perguntar PORTA_GUNICORN "Porta interna do Gunicorn (so 127.0.0.1)" "$PORTA_GUNICORN" '^[0-9]{2,5}$'
    [[ $PORTA_GUNICORN == "$PORTA_ANT" ]] || porta_livre "$PORTA_GUNICORN" && break
    aviso "a porta $PORTA_GUNICORN esta ocupada."
done
perguntar ADMIN_USER "Utilizador administrador do portal" "${ADMIN_USER:-admin}" '^[A-Za-z0-9_.-]{2,64}$'
DEF_ADMIN_PW=s
[[ -f $DIR/data/admin.json ]] && DEF_ADMIN_PW=n
DEFINIR_ADMIN_PW=0
sim_nao "Definir a password do administrador agora?" "$DEF_ADMIN_PW" && DEFINIR_ADMIN_PW=1

# ---------------------------------------------------------------------------- 4. base de dados
titulo "4. Base de dados do portal"
info "Guarda os utilizadores, perfis, bases de dados Oracle configuradas e o historico."
OPCOES_BD=()
[[ $REINSTALAR == 1 && -f $DIR/data/portal.json ]] && OPCOES_BD+=("manter|Manter a configuracao atual (${BD_TIPO:-?} ${BD_LOCAL:-})")
OPCOES_BD+=("sqlite|SQLite - ficheiro local, sem servidor (simples)"
            "mariadb-local|MariaDB neste servidor (instala e cria a base de dados)"
            "mariadb-remota|MariaDB / MySQL noutro servidor"
            "postgresql-local|PostgreSQL neste servidor (instala e cria a base de dados)"
            "postgresql-remota|PostgreSQL noutro servidor")
escolher BD_OPCAO "Onde ficam os dados do portal:" "${OPCOES_BD[0]%%|*}" "${OPCOES_BD[@]}"

BD_PASSWORD=""
if [[ $BD_OPCAO != manter ]]; then
    BD_TIPO=${BD_OPCAO%-*}; BD_LOCAL=${BD_OPCAO#*-}
    [[ $BD_OPCAO == sqlite ]] && { BD_TIPO=sqlite; BD_LOCAL=local; }
fi
if [[ $BD_OPCAO != manter && $BD_TIPO != sqlite ]]; then
    PADRAO_NOME=${NOME//-/_}
    if [[ $BD_LOCAL == local ]]; then
        BD_HOST=127.0.0.1
        BD_PORTA=$([[ $BD_TIPO == mariadb ]] && echo 3306 || echo 5432)
    else
        perguntar BD_HOST "Servidor (nome ou IP)" "${BD_HOST:-}" '^[A-Za-z0-9._:-]+$'
        perguntar BD_PORTA "Porta" "${BD_PORTA:-$([[ $BD_TIPO == mariadb ]] && echo 3306 || echo 5432)}" '^[0-9]{2,5}$'
    fi
    perguntar BD_NOME "Nome da base de dados" "${BD_NOME:-$PADRAO_NOME}" '^[A-Za-z_][A-Za-z0-9_-]{0,62}$'
    perguntar BD_UTIL "Utilizador da base de dados" "${BD_UTIL:-$PADRAO_NOME}" '^[A-Za-z_][A-Za-z0-9_-]{0,31}$'
    if sim_nao "Gerar uma password aleatoria para o utilizador $BD_UTIL?" s; then
        BD_PASSWORD=$(gerar_password)
    else
        ler_password BD_PASSWORD "Password do utilizador $BD_UTIL"
    fi
    if [[ $BD_LOCAL == remota ]]; then
        BD_CLIENTE=$(ip_de_saida "$BD_HOST" || true)
        info "O servidor remoto vai ver as ligacoes a chegar do IP deste servidor."
        perguntar BD_CLIENTE "IP deste servidor visto pelo $BD_HOST" "${BD_CLIENTE:-}" '^[0-9A-Fa-f.:%]+$'
    fi
fi

# ---------------------------------------------------------------------------- 5. nginx / ssl
titulo "5. Nginx e HTTPS"
SERVER_NAME_DEF=${SERVER_NAME:-$(hostname -f 2>/dev/null || echo _)}
info "Nome DNS pelo qual o portal e acedido ('_' = qualquer nome / pelo IP)."
perguntar SERVER_NAME "Nome DNS (server_name)" "$SERVER_NAME_DEF" '^[A-Za-z0-9._* -]+$'
escolher SSL_MODO "HTTPS:" "${SSL_MODO:-nao}" \
    "nao|Sem HTTPS (so HTTP)" \
    "proprio|HTTPS com certificado proprio (indico onde estao os ficheiros)" \
    "letsencrypt|HTTPS com Let's Encrypt (certbot; o nome DNS tem de ser publico)"
perguntar HTTP_PORT "Porta HTTP" "${HTTP_PORT:-80}" '^[0-9]{2,5}$'
HTTPS_PORT=${HTTPS_PORT:-443}
SSL_CADEIA=""
if [[ $SSL_MODO == proprio ]]; then
    perguntar HTTPS_PORT "Porta HTTPS" "$HTTPS_PORT" '^[0-9]{2,5}$'
    info "Certificado em formato PEM. Se o ficheiro nao tiver a cadeia (intermedios), indique-a a seguir."
    while :; do
        perguntar SSL_CERT_ORIG "Certificado (.crt/.pem)" "${SSL_CERT_ORIG:-}" '^/.+$'
        perguntar SSL_KEY_ORIG "Chave privada (.key)" "${SSL_KEY_ORIG:-}" '^/.+$'
        perguntar SSL_CADEIA "Cadeia / intermedios (vazio = nao tem ou ja esta no certificado)" "${SSL_CADEIA_ORIG:-}"
        problema=""
        [[ -r $SSL_CERT_ORIG ]] || problema="certificado nao encontrado"
        [[ -z $problema && -r $SSL_KEY_ORIG ]] || problema=${problema:-"chave nao encontrada"}
        [[ -z $problema && ( -z $SSL_CADEIA || -r $SSL_CADEIA ) ]] || problema=${problema:-"cadeia nao encontrada"}
        if [[ -z $problema ]] && ! openssl x509 -in "$SSL_CERT_ORIG" -noout 2>/dev/null; then problema="o certificado nao e PEM valido"; fi
        if [[ -z $problema ]] && ! openssl pkey -in "$SSL_KEY_ORIG" -passin pass: -noout 2>/dev/null; then
            problema="a chave nao e valida ou tem password (o Nginx precisa da chave sem password)"; fi
        if [[ -z $problema ]] && [[ $(openssl x509 -in "$SSL_CERT_ORIG" -noout -pubkey | sha256sum) != \
                                     $(openssl pkey -in "$SSL_KEY_ORIG" -passin pass: -pubout | sha256sum) ]]; then
            problema="a chave nao corresponde ao certificado"; fi
        if [[ -z $problema ]]; then
            info "$(openssl x509 -in "$SSL_CERT_ORIG" -noout -subject)"
            info "Valido ate: $(openssl x509 -in "$SSL_CERT_ORIG" -noout -enddate | cut -d= -f2)"
            san=$(openssl x509 -in "$SSL_CERT_ORIG" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | xargs || true)
            [[ -n $san ]] && info "Nomes: $san"
            openssl x509 -in "$SSL_CERT_ORIG" -noout -checkend 0 >/dev/null || aviso "o certificado JA EXPIROU."
            [[ $SERVER_NAME != _ && -n $san && $san != *"$SERVER_NAME"* ]] \
                && aviso "o nome '$SERVER_NAME' nao aparece nos nomes do certificado."
            break
        fi
        aviso "$problema."
    done
    SSL_CADEIA_ORIG=$SSL_CADEIA
elif [[ $SSL_MODO == letsencrypt ]]; then
    [[ $SERVER_NAME == _ || $SERVER_NAME == *" "* || $SERVER_NAME == *"*"* ]] \
        && erro "o Let's Encrypt precisa de um unico nome DNS publico (nao '_' nem wildcard)."
    [[ $HTTP_PORT == 80 ]] || aviso "o Let's Encrypt valida pela porta 80; com $HTTP_PORT tem de existir um redirecionamento."
    perguntar LE_EMAIL "Email para avisos do Let's Encrypt" "${LE_EMAIL:-}" '^[^@ ]+@[^@ ]+$'
fi

DESATIVAR_DEFAULT=0
if [[ -e /etc/nginx/sites-enabled/default && $HTTP_PORT == 80 ]]; then
    outros=$(find /etc/nginx/sites-enabled -mindepth 1 ! -name default ! -name "$NOME" 2>/dev/null | wc -l)
    if [[ $SERVER_NAME == _ ]]; then
        sim_nao "Desativar o site 'default' do Nginx (senao o portal so responde pelo nome)?" "$([[ $outros == 0 ]] && echo s || echo n)" \
            && DESATIVAR_DEFAULT=1
    fi
fi

# ---------------------------------------------------------------------------- 6. extras
titulo "6. Backup e firewall"
CRON_BACKUP=0
if sim_nao "Backup diario da configuracao (cron)?" "$([[ ${CRON_HORA:-x} == "" ]] && echo n || echo s)"; then
    CRON_BACKUP=1
    perguntar CRON_HORA "Hora do backup (HH:MM)" "${CRON_HORA:-02:00}" '^([01][0-9]|2[0-3]):[0-5][0-9]$'
else
    CRON_HORA=""
fi
FW=""
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then FW=ufw
elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then FW=firewalld; fi
ABRIR_FW=0
if [[ -n $FW ]]; then
    sim_nao "Firewall ($FW) ativa: abrir as portas do portal?" s && ABRIR_FW=1
fi

# ---------------------------------------------------------------------------- resumo
case $BD_OPCAO in
    manter) BD_RESUMO="manter a atual" ;;
    sqlite) BD_RESUMO="SQLite ($DIR/data/portal.sqlite3)" ;;
    *)      BD_RESUMO="$BD_TIPO $BD_LOCAL - $BD_UTIL@$BD_HOST:$BD_PORTA/$BD_NOME" ;;
esac
case $SSL_MODO in
    nao) URL="http://$([[ $SERVER_NAME == _ ]] && hostname -I | awk '{print $1}' || echo "$SERVER_NAME")$([[ $HTTP_PORT == 80 ]] || echo ":$HTTP_PORT")/" ;;
    *)   URL="https://$([[ $SERVER_NAME == _ ]] && hostname -I | awk '{print $1}' || echo "$SERVER_NAME")$([[ $HTTPS_PORT == 443 ]] || echo ":$HTTPS_PORT")/" ;;
esac
titulo "Resumo"
printf "   %-22s %s\n" \
    "Instancia / servico" "$NOME  ($NOME.service)" \
    "Pasta" "$DIR" \
    "Utilizador de servico" "$UTIL" \
    "Codigo" "$([[ $COD == git ]] && echo "git: $GIT_URL ($GIT_BRANCH) $GIT_PASTA" || echo "$COD")" \
    "Gunicorn" "127.0.0.1:$PORTA_GUNICORN" \
    "Administrador" "$ADMIN_USER$([[ $DEFINIR_ADMIN_PW == 1 ]] && echo " (definir password)")" \
    "Base de dados" "$BD_RESUMO" \
    "Nginx" "$SERVER_NAME, HTTP $HTTP_PORT$([[ $SSL_MODO != nao ]] && echo ", HTTPS $HTTPS_PORT ($SSL_MODO)")" \
    "Endereco" "$URL" \
    "Backup cron" "$([[ $CRON_BACKUP == 1 ]] && echo "todos os dias as $CRON_HORA" || echo nao)" \
    "Firewall" "$([[ $ABRIR_FW == 1 ]] && echo "abrir portas ($FW)" || echo "nao alterar")"
echo
sim_nao "Avancar com a instalacao?" s || { echo "Cancelado, nada foi alterado."; exit 0; }

# ============================================================================ execucao
titulo "Pacotes"
if [[ $PKG == apt ]]; then
    apt-get update -q >/dev/null
    PKGS=(python3 python3-venv python3-pip nginx git curl openssl tar iproute2)
    [[ $BD_OPCAO == mariadb-local ]] && ! command -v mariadb >/dev/null && ! command -v mysqld >/dev/null && PKGS+=(mariadb-server)
    [[ $BD_OPCAO == postgresql-local ]] && ! command -v psql >/dev/null && PKGS+=(postgresql)
    [[ $SSL_MODO == letsencrypt ]] && PKGS+=(certbot python3-certbot-nginx)
    [[ $CRON_BACKUP == 1 ]] && PKGS+=(cron)
else
    PKGS=(nginx git curl openssl tar iproute)
    [[ $BD_OPCAO == mariadb-local ]] && ! command -v mariadb >/dev/null && PKGS+=(mariadb-server)
    [[ $BD_OPCAO == postgresql-local ]] && ! command -v psql >/dev/null && PKGS+=(postgresql-server)
    [[ $SSL_MODO == letsencrypt ]] && PKGS+=(certbot python3-certbot-nginx)
    [[ $CRON_BACKUP == 1 ]] && PKGS+=(cronie)
fi
instalar_pacotes "${PKGS[@]}"
if ! escolher_python; then
    [[ $PKG == dnf ]] && { instalar_pacotes python3.12 python3.12-pip || instalar_pacotes python3.11 python3.11-pip || true; }
    escolher_python || erro "e preciso Python 3.10 ou superior."
fi
ok "Python: $($PY --version)"

titulo "Utilizador e codigo"
if ! id "$UTIL" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$DIR" --no-create-home --shell /usr/sbin/nologin "$UTIL"
    ok "utilizador $UTIL criado"
fi
mkdir -p "$DIR"
case $COD in
    local) copiar_codigo "$SCRIPT_DIR"; ok "codigo copiado de $SCRIPT_DIR" ;;
    git)
        TMP_COD=$(mktemp -d)
        extrair_pasta_git "$GIT_URL" "$GIT_BRANCH" "$GIT_PASTA" "$TMP_COD" || { rm -rf "$TMP_COD"; erro "nao foi possivel obter o codigo do Git."; }
        tem_codigo "$TMP_COD" || { rm -rf "$TMP_COD"; erro "a pasta '$GIT_PASTA' nao tem o portal (wsgi.py, app/, deploy/)."; }
        copiar_codigo "$TMP_COD"; rm -rf "$TMP_COD"
        ok "codigo obtido do Git" ;;
    manter) ok "codigo mantido" ;;
esac
tem_codigo "$DIR" || erro "$DIR nao tem o codigo do portal."

mkdir -p "$DIR/data/backups"
[[ -f $DIR/.env ]] || cp "$DIR/.env.example" "$DIR/.env"
set_env PORTAL_ADMIN_USER "$ADMIN_USER"
set_env PORTAL_COOKIE_SECURE "$([[ $SSL_MODO == nao ]] && echo 0 || echo 1)"
TZ_SIS=$(timedatectl show -p Timezone --value 2>/dev/null || true)
[[ -n $TZ_SIS ]] && set_env TZ "$TZ_SIS"
# O codigo e do root (o utilizador de servico so le): o root volta a correr este script e os
# templates, por isso o servico nao os pode alterar. data/ e .venv sao do utilizador de servico.
mkdir -p "$DIR/.venv"
find "$DIR" \( -path "$DIR/data" -o -path "$DIR/.venv" \) -prune -o \
     -exec chown root:"$UTIL" {} + -exec chmod u=rwX,g=rX,o= {} +
chmod 755 "$DIR" "$DIR/app"
chmod -R o+rX "$DIR/app/static"
chmod 750 "$DIR"/instalar.sh "$DIR"/deploy/bd/*.sh
chown -R "$UTIL:$UTIL" "$DIR/data" "$DIR/.venv"
chmod 700 "$DIR/data"
chmod 640 "$DIR/.env"
ok "permissoes: codigo do root (so leitura para $UTIL), data/ e .venv de $UTIL, app/static legivel pelo Nginx"
case $DIR in /home/*|/root/*) aviso "pastas em /home ou /root costumam nao ser acessiveis ao Nginx (static).";; esac

titulo "Ambiente Python"
[[ -x $DIR/.venv/bin/python ]] || como_portal "$PY" -m venv "$DIR/.venv"   # a pasta ja existe e e de $UTIL
como_portal env PIP_NO_CACHE_DIR=1 "$DIR/.venv/bin/pip" install -q --disable-pip-version-check --upgrade pip
como_portal env PIP_NO_CACHE_DIR=1 "$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"
ok "pacotes Python instalados em $DIR/.venv"

# ---------------------------------------------------------------------------- base de dados
titulo "Base de dados do portal"
export PORTAL_DB_PASSWORD="$BD_PASSWORD"
config_storage() {   # grava e testa; devolve !=0 se falhar
    manage set-storage --type "$BD_TIPO" --host "$BD_HOST" --port "$BD_PORTA" \
        --database "$BD_NOME" --user "$BD_UTIL"
}
case $BD_OPCAO in
    manter) ok "mantida: $(manage show-storage)" ;;
    sqlite) manage set-storage --type sqlite ;;
    mariadb-local)
        systemctl enable --now mariadb 2>/dev/null || systemctl enable --now mysql 2>/dev/null || systemctl enable --now mysqld
        "$DIR/deploy/bd/preparar-mariadb.sh" --bd "$BD_NOME" --utilizador "$BD_UTIL" \
            --cliente "localhost,127.0.0.1" --sem-rede --sem-firewall -y
        config_storage ;;
    postgresql-local)
        if [[ $PKG == dnf && ! -s /var/lib/pgsql/data/PG_VERSION ]]; then postgresql-setup --initdb; fi
        systemctl enable --now postgresql
        "$DIR/deploy/bd/preparar-postgresql.sh" --bd "$BD_NOME" --utilizador "$BD_UTIL" \
            --cliente 127.0.0.1 --sem-rede --sem-firewall -y
        config_storage ;;
    mariadb-remota|postgresql-remota)
        mkdir -p "$DIR/data/bd"
        T_BD=$BD_NOME T_UTILIZADOR=$BD_UTIL T_PASSWORD=${BD_PASSWORD//\'/\'\'} T_CLIENTE=$BD_CLIENTE \
            render "$DIR/deploy/bd/$BD_TIPO.sql" "$DIR/data/bd/$BD_TIPO.sql"
        chown -R "$UTIL:$UTIL" "$DIR/data/bd"; chmod 700 "$DIR/data/bd"; chmod 600 "$DIR/data/bd/$BD_TIPO.sql"
        mostrar_instrucoes() {
            echo
            echo "   ${C_NG}Preparar o servidor $BD_HOST${C_0} (como root, no servidor $BD_TIPO):"
            echo
            echo "     scp $DIR/deploy/bd/preparar-$BD_TIPO.sh <utilizador>@$BD_HOST:/tmp/"
            echo "     sudo PORTAL_DB_PASSWORD=$(printf %q "$BD_PASSWORD") bash /tmp/preparar-$BD_TIPO.sh \\"
            echo "          --bd $BD_NOME --utilizador $BD_UTIL --cliente $BD_CLIENTE"
            echo
            if [[ $BD_TIPO == postgresql ]]; then
                echo "   O script cria o utilizador e a base de dados, acrescenta ao pg_hba.conf"
                echo "     host    $BD_NOME    $BD_UTIL    $BD_CLIENTE/32    scram-sha-256"
                echo "   poe o PostgreSQL a escutar na rede (se preciso) e abre a firewall."
            else
                echo "   O script cria a base de dados e '$BD_UTIL'@'$BD_CLIENTE', poe o MariaDB a"
                echo "   escutar na rede (bind-address, se preciso) e abre a firewall."
            fi
            echo "   So SQL, para fazer a mao: $DIR/data/bd/$BD_TIPO.sql (inclui os passos de rede em comentario)."
            echo
        }
        mostrar_instrucoes
        while :; do
            read -r -p "   Enter = testar a ligacao | i = instrucoes | s = continuar com SQLite e mudar depois: " r
            case ${r,,} in
                i) mostrar_instrucoes ;;
                s) aviso "fica SQLite. Depois: Configuracao > Portal > Armazenamento, ou volte a correr este script."
                   manage set-storage --type sqlite; BD_TIPO_EFETIVO=sqlite; break ;;
                *) if config_storage; then break; fi
                   aviso "sem ligacao. Confirme que o script correu no $BD_HOST e que a porta $BD_PORTA esta acessivel:"
                   info "  (timeout 3 bash -c '</dev/tcp/$BD_HOST/$BD_PORTA') && echo porta aberta" ;;
            esac
        done ;;
esac
unset PORTAL_DB_PASSWORD

# ---------------------------------------------------------------------------- administrador
if [[ $DEFINIR_ADMIN_PW == 1 ]]; then
    titulo "Password do administrador ($ADMIN_USER)"
    until ( cd "$DIR" && como_portal "$DIR/.venv/bin/python" manage.py set-admin-password --user "$ADMIN_USER" ); do
        aviso "tente outra vez."
    done
fi

# ---------------------------------------------------------------------------- systemd
titulo "Servico systemd ($NOME.service)"
T_NOME=$NOME T_DIR=$DIR T_UTILIZADOR=$UTIL T_BIND="127.0.0.1:$PORTA_GUNICORN" \
    render "$DIR/deploy/portal.service.tpl" "/etc/systemd/system/$NOME.service"
systemctl daemon-reload
systemctl enable -q "$NOME"
systemctl restart "$NOME"
CODIGO=000
for _ in $(seq 1 20); do
    CODIGO=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORTA_GUNICORN/login" || true)
    [[ $CODIGO == 200 ]] && break
    sleep 1
done
if [[ $CODIGO == 200 ]]; then ok "Gunicorn a responder em 127.0.0.1:$PORTA_GUNICORN"
else aviso "o Gunicorn nao respondeu (HTTP $CODIGO). Veja: journalctl -u $NOME -n 50"; fi

# ---------------------------------------------------------------------------- nginx
titulo "Nginx"
if [[ -d /etc/nginx/sites-available ]]; then
    NGX_CONF=/etc/nginx/sites-available/$NOME; NGX_LINK=/etc/nginx/sites-enabled/$NOME
else
    NGX_CONF=/etc/nginx/conf.d/$NOME.conf; NGX_LINK=""
fi
NGX_ANT=""
[[ -f $NGX_CONF ]] && { NGX_ANT=$(mktemp); cp -p "$NGX_CONF" "$NGX_ANT"; }

T_NOME=$NOME; T_DIR=$DIR; T_BIND="127.0.0.1:$PORTA_GUNICORN"; T_SERVER_NAME=$SERVER_NAME
T_HTTP_PORT=$HTTP_PORT; T_HTTPS_PORT=$HTTPS_PORT
if [[ $SSL_MODO == proprio ]]; then
    if [[ -n $SSL_CADEIA_ORIG ]]; then
        # o Nginx quer o certificado e a cadeia no mesmo ficheiro
        mkdir -p "/etc/nginx/ssl/$NOME"; chmod 700 "/etc/nginx/ssl/$NOME"
        { cat "$SSL_CERT_ORIG"; echo; cat "$SSL_CADEIA_ORIG"; } | sed '/^$/d' > "/etc/nginx/ssl/$NOME/fullchain.pem"
        install -m 600 "$SSL_KEY_ORIG" "/etc/nginx/ssl/$NOME/privkey.pem"
        T_SSL_CERT=/etc/nginx/ssl/$NOME/fullchain.pem; T_SSL_KEY=/etc/nginx/ssl/$NOME/privkey.pem
        info "certificado + cadeia em /etc/nginx/ssl/$NOME/ (ao renovar, volte a correr o script)"
    else
        T_SSL_CERT=$SSL_CERT_ORIG; T_SSL_KEY=$SSL_KEY_ORIG
    fi
    T_HTTPS_URL="https://\$host$([[ $HTTPS_PORT == 443 ]] || echo ":$HTTPS_PORT")"
    render "$DIR/deploy/nginx-https.conf.tpl" "$NGX_CONF"
else
    render "$DIR/deploy/nginx-http.conf.tpl" "$NGX_CONF"
fi
[[ -n $NGX_LINK ]] && ln -sf "$NGX_CONF" "$NGX_LINK"
[[ -z $NGX_LINK && $SERVER_NAME == _ && $HTTP_PORT == 80 ]] \
    && aviso "com server_name '_' na porta 80, o servidor 'default' do /etc/nginx/nginx.conf pode responder primeiro; use um nome DNS ou outra porta."
[[ $DESATIVAR_DEFAULT == 1 ]] && rm -f /etc/nginx/sites-enabled/default && info "site 'default' desativado"
if ! nginx -t 2>/tmp/nginx-teste.$$; then
    cat /tmp/nginx-teste.$$ >&2; rm -f /tmp/nginx-teste.$$
    if [[ -n $NGX_ANT ]]; then cp -p "$NGX_ANT" "$NGX_CONF"; else rm -f "$NGX_CONF" ${NGX_LINK:+"$NGX_LINK"}; fi
    erro "configuracao do Nginx invalida; a anterior foi reposta. Corrija e volte a correr o script."
fi
rm -f /tmp/nginx-teste.$$ ${NGX_ANT:+"$NGX_ANT"}
systemctl enable -q nginx
systemctl reload nginx 2>/dev/null || systemctl restart nginx
ok "site $NGX_CONF"

if command -v getenforce >/dev/null && [[ $(getenforce) == Enforcing ]]; then
    setsebool -P httpd_can_network_connect 1
    chcon -R -t httpd_sys_content_t "$DIR/app/static" 2>/dev/null || true
    ok "SELinux: Nginx autorizado a ligar ao Gunicorn"
fi

if [[ $SSL_MODO == letsencrypt ]]; then
    certbot --nginx -n --agree-tos -m "$LE_EMAIL" -d "$SERVER_NAME" --redirect \
        && ok "certificado Let's Encrypt instalado (renovacao automatica pelo certbot)" \
        || { aviso "o certbot falhou: o portal fica em HTTP. Corrija o DNS/porta 80 e volte a correr o script."
             set_env PORTAL_COOKIE_SECURE 0; systemctl restart "$NOME"
             URL="http://$SERVER_NAME$([[ $HTTP_PORT == 80 ]] || echo ":$HTTP_PORT")/"; }
fi

# ---------------------------------------------------------------------------- cron / firewall
if [[ $CRON_BACKUP == 1 ]]; then
    cat > "/etc/cron.d/$NOME" <<EOF
# Portal Oracle ($NOME): backup diario da configuracao
${CRON_HORA#*:} ${CRON_HORA%:*} * * * $UTIL cd $DIR && .venv/bin/python manage.py backup --prune >> data/cron.log 2>&1
EOF
    chmod 644 "/etc/cron.d/$NOME"
    ok "backup diario as $CRON_HORA (/etc/cron.d/$NOME)"
else
    rm -f "/etc/cron.d/$NOME"
fi
grep -qE '^PORTAL_FERNET_KEY=.' "$DIR/.env" && [[ $CRON_BACKUP == 1 ]] \
    && aviso "PORTAL_FERNET_KEY esta definido no .env: o cron nao o le (ver README)."

if [[ $ABRIR_FW == 1 ]]; then
    PORTAS=("$HTTP_PORT"); [[ $SSL_MODO != nao ]] && PORTAS+=("$HTTPS_PORT")
    for p in "${PORTAS[@]}"; do
        if [[ $FW == ufw ]]; then ufw allow "$p/tcp" >/dev/null
        else firewall-cmd -q --permanent --add-port="$p/tcp"; fi
    done
    [[ $FW == firewalld ]] && firewall-cmd -q --reload
    ok "firewall: portas ${PORTAS[*]}/tcp abertas"
fi

# ---------------------------------------------------------------------------- estado
[[ ${BD_TIPO_EFETIVO:-} == sqlite ]] && { BD_TIPO=sqlite; BD_LOCAL=local; }
{
    echo "# Respostas da ultima execucao do instalar.sh (sem passwords). Usadas como valores por omissao."
    for v in NOME DIR UTIL GIT_URL GIT_BRANCH GIT_PASTA PORTA_GUNICORN ADMIN_USER BD_TIPO BD_LOCAL BD_HOST \
             BD_PORTA BD_NOME BD_UTIL BD_CLIENTE SERVER_NAME SSL_MODO HTTP_PORT HTTPS_PORT SSL_CERT_ORIG \
             SSL_KEY_ORIG SSL_CADEIA_ORIG LE_EMAIL CRON_HORA; do
        printf '%s=%q\n' "$v" "${!v:-}"
    done
} > "$DIR/$ESTADO_FICHEIRO"
chown root:root "$DIR/$ESTADO_FICHEIRO"; chmod 600 "$DIR/$ESTADO_FICHEIRO"

# ---------------------------------------------------------------------------- fim
titulo "Concluido"
printf "   %-20s %s\n" \
    "Portal" "$URL  (utilizador $ADMIN_USER)" \
    "Estado / reiniciar" "systemctl status $NOME  |  systemctl restart $NOME" \
    "Logs" "journalctl -u $NOME -f  |  /var/log/nginx/$NOME.*.log" \
    "Base de dados" "$(manage show-storage 2>/dev/null || echo '?')" \
    "Nginx" "$NGX_CONF" \
    "Reconfigurar" "sudo $DIR/instalar.sh" \
    "Remover" "sudo $DIR/instalar.sh --desinstalar $NOME"
[[ ${BD_LOCAL:-} == remota && ${BD_TIPO:-} != sqlite ]] && info "SQL gerado: $DIR/data/bd/$BD_TIPO.sql"
[[ ! -f $DIR/data/admin.json ]] && aviso "ainda nao ha administrador: cd $DIR && sudo -u $UTIL .venv/bin/python manage.py set-admin-password"
echo
info "${C_NG}Faca backup de $DIR/data${C_0} (sobretudo fernet.key e admin.json)."
