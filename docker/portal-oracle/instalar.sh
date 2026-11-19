#!/usr/bin/env bash
#
# instalar.sh - instalacao e configuracao do Portal Oracle num servidor (sem Docker).
# Ajuda completa: ./instalar.sh --help
#
set -Eeuo pipefail
shopt -u patsub_replacement 2>/dev/null || true   # bash 5.2: "&" na substituicao

# Repositorio por omissao para a opcao "Git" (vazio = perguntar). Ex.: https://github.com/<utilizador>/projetos.git
REPO_URL_PADRAO=""
REPO_BRANCH_PADRAO="main"
REPO_PASTA_PADRAO="docker/portal-oracle"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ESTADO_DIR="/etc/portal-oracle"          # registo das instancias: <nome>.conf (respostas, sem passwords)
ESTADO_LEGADO=".instalacao"              # versoes anteriores guardavam na pasta da instalacao
AUTO=0                                   # 1 = aceitar os valores guardados sem perguntar (reaplicar)
ESTADO_VARS=(NOME DIR UTIL GIT_URL GIT_BRANCH GIT_PASTA PORTA_GUNICORN ADMIN_USER BD_TIPO BD_LOCAL BD_HOST
             BD_PORTA BD_NOME BD_UTIL BD_CLIENTE SERVER_NAME SSL_MODO HTTP_PORT HTTPS_PORT SSL_CERT_ORIG
             SSL_KEY_ORIG SSL_CADEIA_ORIG LE_EMAIL CRON_HORA ABRIR_FW ESTADO_INST ULTIMA_EXEC)

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
    if (( AUTO )) && { [[ -z $__re ]] || [[ $__d =~ $__re ]]; }; then
        echo "   $__t: ${__d:--}"; printf -v "$__v" '%s' "$__d"; return 0
    fi
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
    if (( AUTO )); then
        echo "   $1 $([[ $__d == s ]] && echo sim || echo nao)"; [[ $__d == s ]]; return
    fi
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
        (( AUTO )) || printf "     %d) %s\n" "$i" "${op#*|}"
        [[ ${op%%|*} == "$__d" ]] && (( AUTO )) && echo "     -> ${op#*|}"
        i=$((i + 1))
    done
    if (( AUTO )); then printf -v "$__v" '%s' "${chaves[def_n - 1]}"; return 0; fi
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

ajuda() {
    cat <<'EOF'
instalar.sh - instalacao e configuracao do Portal Oracle num servidor (sem Docker)

Uso:
  sudo ./instalar.sh                  menu: escolher a instancia (ou criar uma nova)
  sudo ./instalar.sh NOME             instalar ou reconfigurar a instancia NOME
  sudo ./instalar.sh --reaplicar NOME reaplicar a configuracao guardada, sem perguntas (mantem o codigo)
  sudo ./instalar.sh --atualizar NOME obter o codigo do Git e reaplicar, sem perguntas
  sudo ./instalar.sh --mostrar [NOME] mostrar a configuracao guardada
  sudo ./instalar.sh --lista          listar as instancias deste servidor
  sudo ./instalar.sh --verificar [NOME]   diagnostico (portal indisponivel?): servico, Gunicorn,
                                      permissoes de data/ e .venv, ficheiros a mais, Nginx, registo
  sudo ./instalar.sh --manutencao NOME on|off|status   modo de manutencao do portal
  sudo ./instalar.sh --desinstalar [NOME]
  ./instalar.sh --help                esta ajuda (tambem -h, --ajuda)

O que pergunta (Enter aceita o valor entre [ ]; nada e alterado antes da confirmacao):
  1. Instancia     nome (ex. portal-oracle, nome-portal), pasta (/opt/<nome>), utilizador de servico
  2. Codigo        copiar da pasta deste script, obter so a pasta do portal do Git (sparse-checkout),
                   ou manter o codigo instalado
  3. Aplicacao     porta interna do Gunicorn, administrador do portal e password
  4. Base de dados SQLite; MariaDB ou PostgreSQL neste servidor (instala e cria); ou noutro servidor
                   (gera o SQL e o script a correr la, incluindo pg_hba.conf, e testa a ligacao)
  5. Nginx         nome DNS, portas, sem HTTPS / certificado proprio / Let's Encrypt
  6. Extras        backup diario no cron, abrir as portas na firewall (ufw ou firewalld)

Configuracao guardada:
  As respostas (sem passwords) ficam em /etc/portal-oracle/<nome>.conf, gravadas logo apos a
  confirmacao. Ao voltar a correr o script, a configuracao e mostrada e pode:
    - rever as respostas (os valores guardados aparecem como omissao)
    - reaplicar sem perguntas (ex. depois de atualizar o script ou os templates)
    - atualizar o codigo pelo Git e reaplicar
  data/, .env e .venv nunca sao substituidos.

Atualizar o codigo (sincronizar com o repositorio):
  Use --atualizar (ou o menu). O script substitui o codigo (app/, deploy/, ...) para nao ficarem
  ficheiros antigos, remove o que e so do Docker (Dockerfile, docker-compose.yml, .dockerignore,
  docker/), corrige as permissoes, atualiza os pacotes Python e reinicia o servico.
  NAO copie o repositorio diretamente por cima de /opt/<nome>: os ficheiros ficam do root (o servico
  deixa de conseguir escrever em data/) e o Gunicorn continua com o codigo antigo carregado. Se ja o
  fez: sudo ./instalar.sh --verificar NOME  e  sudo ./instalar.sh --reaplicar NOME

O que cria (exemplo com o nome "nome-portal"):
  /opt/nome-portal                       codigo (do root), data/ e .venv (do utilizador de servico)
  /etc/systemd/system/nome-portal.service
  /etc/nginx/sites-available/nome-portal (RHEL: /etc/nginx/conf.d/nome-portal.conf)
  /etc/cron.d/nome-portal                backup diario (opcional)
  /etc/portal-oracle/nome-portal.conf    configuracao guardada

Repositorio por omissao para a opcao Git: variavel REPO_URL_PADRAO no inicio deste script.
Mais detalhes: README.md, seccao "Instalacao num servidor (sem Docker)".
EOF
}

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

# Ficheiros so para Docker: numa instalacao em servidor nao servem e sao removidos
DOCKER_FICHEIROS=(Dockerfile docker-compose.yml .dockerignore docker)
# Nunca substituidos nem apagados na pasta da instalacao
PRESERVAR=(data .env .venv "$ESTADO_LEGADO")

limpar_docker() {
    local f n=0
    for f in "${DOCKER_FICHEIROS[@]}"; do
        [[ -e $DIR/$f ]] && { rm -rf -- "${DIR:?}/$f"; n=$((n + 1)); }
    done
    (( n )) && ok "ficheiros do Docker removidos ($n)"
    return 0
}

copiar_codigo() {   # copiar_codigo ORIGEM  -> $DIR (nunca toca em data/, .env, .venv)
    local src=$1 item nome p
    [[ $(realpath "$src") == $(realpath "$DIR") ]] && return 0
    # Substitui cada entrada de codigo da origem (ex. app/ inteira), para nao ficarem ficheiros
    # antigos (modulos, templates ou rotas que ja nao existem). O que nao vem na origem fica.
    for item in "$src"/* "$src"/.[!.]*; do
        [[ -e $item ]] || continue
        nome=${item##*/}
        for p in "${PRESERVAR[@]}" .git "${DOCKER_FICHEIROS[@]}"; do [[ $nome == "$p" ]] && continue 2; done
        rm -rf -- "${DIR:?}/$nome"
    done
    local excl=(--exclude=./.git --exclude='__pycache__')
    for p in "${PRESERVAR[@]}" "${DOCKER_FICHEIROS[@]}"; do excl+=("--exclude=./$p"); done
    tar -C "$src" "${excl[@]}" -cf - . | tar -C "$DIR" --no-same-owner -xf -
    limpar_docker
}

tem_codigo() { [[ -f $1/wsgi.py && -f $1/manage.py && -d $1/app && -d $1/deploy ]]; }

# ============================================================================ configuracao guardada
listar_instancias() {   # nomes das instancias registadas (incluindo as de versoes anteriores)
    {
        for f in "$ESTADO_DIR"/*.conf; do [[ -f $f ]] && basename "$f" .conf; done
        for u in /etc/systemd/system/*.service; do
            [[ -f $u ]] && grep -q '^Description=Portal Oracle' "$u" && basename "$u" .service
        done
        true
    } 2>/dev/null | sort -u
}

carregar_estado() {   # carregar_estado NOME -> define as variaveis guardadas; 1 se nao houver
    local nome=$1 f dir v
    f="$ESTADO_DIR/$nome.conf"
    if [[ ! -f $f ]]; then
        dir=$(sed -n 's/^WorkingDirectory=//p' "/etc/systemd/system/$nome.service" 2>/dev/null || true)
        f="${dir:-/opt/$nome}/$ESTADO_LEGADO"
    fi
    [[ -f $f ]] || return 1
    for v in "${ESTADO_VARS[@]}"; do unset "$v"; done
    # shellcheck disable=SC1090
    source "$f"
    NOME=$nome
    ESTADO_ORIGEM=$f
}

gravar_estado() {   # gravar_estado incompleta|concluida
    local v
    ESTADO_INST=$1; ULTIMA_EXEC=$(date '+%Y-%m-%d %H:%M')
    install -d -m 700 "$ESTADO_DIR"
    {
        echo "# Portal Oracle - instancia '$NOME'. Gerado pelo instalar.sh (sem passwords)."
        echo "# Valores por omissao ao voltar a correr: sudo instalar.sh $NOME"
        for v in "${ESTADO_VARS[@]}"; do printf '%s=%q\n' "$v" "${!v:-}"; done
    } > "$ESTADO_DIR/$NOME.conf"
    chmod 600 "$ESTADO_DIR/$NOME.conf"
    ESTADO_ORIGEM="$ESTADO_DIR/$NOME.conf"
    rm -f "$DIR/$ESTADO_LEGADO"
}

calcular_url() {
    local host=${SERVER_NAME:-_}
    [[ $host == _ ]] && host=$(hostname -I 2>/dev/null | awk '{print $1}')
    host=${host%% *}
    if [[ ${SSL_MODO:-nao} == nao ]]; then
        URL="http://$host$([[ ${HTTP_PORT:-80} == 80 ]] || echo ":$HTTP_PORT")/"
    else
        URL="https://$host$([[ ${HTTPS_PORT:-443} == 443 ]] || echo ":$HTTPS_PORT")/"
    fi
}

mostrar_estado() {   # mostra as variaveis carregadas
    local bd ssl srv
    case ${BD_TIPO:-sqlite} in
        sqlite) bd="SQLite (${DIR:-}/data/portal.sqlite3)" ;;
        *)      bd="$BD_TIPO ${BD_LOCAL:-} - ${BD_UTIL:-}@${BD_HOST:-}:${BD_PORTA:-}/${BD_NOME:-}" ;;
    esac
    case ${SSL_MODO:-nao} in
        proprio)     ssl="certificado proprio: ${SSL_CERT_ORIG:-}${SSL_CADEIA_ORIG:+ (+ cadeia)}" ;;
        letsencrypt) ssl="Let's Encrypt (${LE_EMAIL:-})" ;;
        *)           ssl="sem HTTPS" ;;
    esac
    srv=$(systemctl is-active "$NOME" 2>/dev/null || true)
    calcular_url
    printf "   %-22s %s\n" \
        "Instancia / servico" "$NOME  ($NOME.service: ${srv:-?})" \
        "Pasta" "${DIR:-}" \
        "Utilizador de servico" "${UTIL:-}" \
        "Codigo (Git)" "$([[ -n ${GIT_URL:-} ]] && echo "$GIT_URL ($GIT_BRANCH) $GIT_PASTA" || echo "-")" \
        "Gunicorn" "127.0.0.1:${PORTA_GUNICORN:-}" \
        "Administrador" "${ADMIN_USER:-}" \
        "Base de dados" "$bd" \
        "Nginx" "${SERVER_NAME:-}, HTTP ${HTTP_PORT:-80}$([[ ${SSL_MODO:-nao} != nao ]] && echo ", HTTPS ${HTTPS_PORT:-443}")" \
        "HTTPS" "$ssl" \
        "Endereco" "$URL" \
        "Backup cron" "$([[ -n ${CRON_HORA:-} ]] && echo "todos os dias as $CRON_HORA" || echo nao)" \
        "Firewall" "$([[ ${ABRIR_FW:-0} == 1 ]] && echo "portas abertas pelo script" || echo "nao alterada")" \
        "Ultima execucao" "${ULTIMA_EXEC:-?}$([[ ${ESTADO_INST:-} == incompleta ]] && echo "  (INCOMPLETA)")" \
        "Guardado em" "${ESTADO_ORIGEM:-}"
}

listar() {
    local n; local -a lista
    mapfile -t lista < <(listar_instancias)
    if (( ${#lista[@]} == 0 )); then echo "Nao ha instancias do portal neste servidor."; return 0; fi
    printf "%-20s %-24s %-9s %-36s %s\n" NOME PASTA SERVICO ENDERECO "ULTIMA EXECUCAO"
    for n in "${lista[@]}"; do
        if carregar_estado "$n"; then
            calcular_url
            printf "%-20s %-24s %-9s %-36s %s\n" "$n" "${DIR:-?}" "$(systemctl is-active "$n" 2>/dev/null || true)" \
                "$URL" "${ULTIMA_EXEC:-?}$([[ ${ESTADO_INST:-} == incompleta ]] && echo ' (incompleta)')"
        else
            printf "%-20s %-24s %-9s %s\n" "$n" "?" "$(systemctl is-active "$n" 2>/dev/null || true)" "(sem configuracao guardada)"
        fi
    done
}

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
    AUTO=0
    perguntar nome "Nome da instancia" "${1:-}" '^[a-z][a-z0-9-]{1,30}$'
    unit="/etc/systemd/system/$nome.service"
    dir=$(sed -n 's/^WorkingDirectory=//p' "$unit" 2>/dev/null || true)
    util=$(sed -n 's/^User=//p' "$unit" 2>/dev/null || true)
    [[ -z $dir && $NOME == "$nome" ]] && dir=${DIR:-}
    [[ -n $dir ]] || perguntar dir "Pasta de instalacao" "/opt/$nome"
    info "Servico: $unit"
    info "Pasta:   $dir"
    sim_nao "Parar e remover o servico, o site do Nginx e o cron de '$nome'?" n || exit 0

    systemctl disable --now "$nome" 2>/dev/null || true
    rm -f "$unit" "/etc/cron.d/$nome" "$ESTADO_DIR/$nome.conf"
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

# ============================================================================ verificar
verificar() {   # diagnostico de uma instancia (portal indisponivel?)
    local unit="/etc/systemd/system/$NOME.service" cod dono modo f problemas=0
    titulo "Verificar '$NOME'"
    falha() { aviso "$*"; problemas=$((problemas + 1)); }
    [[ -f $unit ]] && ok "servico $unit" || falha "falta o servico $unit"
    if systemctl is-active -q "$NOME" 2>/dev/null; then ok "servico ativo"
    else falha "servico parado: $(systemctl is-active "$NOME" 2>/dev/null || true)"; fi
    cod=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORTA_GUNICORN:-8000}/login" || true)
    case $cod in
        200) ok "Gunicorn responde em 127.0.0.1:${PORTA_GUNICORN:-8000}" ;;
        503) ok "Gunicorn responde (modo de manutencao ativo)" ;;
        *)   falha "Gunicorn nao responde em 127.0.0.1:${PORTA_GUNICORN:-8000} (HTTP $cod)" ;;
    esac
    for f in data .venv; do
        [[ -d $DIR/$f ]] || { falha "falta $DIR/$f"; continue; }
        dono=$(stat -c '%U' "$DIR/$f")
        [[ $dono == "$UTIL" ]] && ok "$f/ e de $UTIL" || falha "$f/ e de '$dono' (devia ser $UTIL): ficheiros copiados a mao?"
        if [[ $f == data ]]; then
            [[ -z $(find "$DIR/data" ! -user "$UTIL" -print -quit) ]] || falha "ha ficheiros em data/ que nao sao de $UTIL"
        fi
    done
    modo=$(stat -c '%a' "$DIR/data" 2>/dev/null || echo "?")
    [[ $modo == 700 ]] && ok "data/ com permissoes 700" || falha "data/ com permissoes $modo (devia ser 700)"
    if runuser -u "$UTIL" -- test -r "$DIR/wsgi.py" -a -r "$DIR/app/__init__.py" 2>/dev/null; then ok "codigo legivel por $UTIL"
    else falha "o codigo nao e legivel por $UTIL"; fi
    for f in "${DOCKER_FICHEIROS[@]}"; do [[ -e $DIR/$f ]] && falha "ficheiro do Docker na instalacao: $DIR/$f"; done
    for f in app/oracle.py app/jobs.py app/views/ops.py; do [[ -e $DIR/$f ]] && falha "ficheiro antigo (antes dos modulos): $DIR/$f"; done
    if command -v nginx >/dev/null; then
        nginx -t >/dev/null 2>&1 && ok "configuracao do Nginx valida" || falha "nginx -t falha"
        systemctl is-active -q nginx && ok "Nginx ativo" || falha "Nginx parado"
    fi
    info "Manutencao: $(manage maintenance status 2>/dev/null | head -1 || echo '?')"
    echo
    info "Ultimas linhas do registo (journalctl -u $NOME):"
    journalctl -u "$NOME" -n 15 --no-pager 2>/dev/null | sed 's/^/     /' || true
    echo
    if (( problemas )); then
        aviso "$problemas problema(s). Normalmente resolve: sudo $0 --reaplicar $NOME"
        info "(so permissoes de data/ e .venv: sudo systemctl restart $NOME)"
    else
        ok "sem problemas encontrados"
    fi
    exit 0
}

# ============================================================================ inicio
MODO=""; NOME_ARG=""
case "${1:-}" in
    -h|--help|--ajuda) ajuda; exit 0 ;;
    --desinstalar|--reaplicar|--atualizar|--mostrar|--lista|--listar|--verificar) MODO=${1#--}; NOME_ARG=${2:-} ;;
    --manutencao)
        MODO=manutencao; NOME_ARG=${2:-}; MANUT_ACAO=${3:-status}
        [[ $MANUT_ACAO =~ ^(on|off|status)$ ]] || { echo "Uso: $0 --manutencao NOME on|off|status" >&2; exit 2; } ;;
    -*) echo "Opcao desconhecida: $1 (ver $0 --help)" >&2; exit 2 ;;
    *) NOME_ARG=${1:-} ;;
esac
[[ -z $NOME_ARG || $NOME_ARG =~ ^[a-z][a-z0-9-]{1,30}$ ]] || erro "nome de instancia invalido: $NOME_ARG"
[[ $EUID -eq 0 ]] || erro "corra como root: sudo $0 $*"
# Se vier por pipe (curl | bash), as perguntas leem do terminal
if [[ ! -t 0 ]] && { : </dev/tty; } 2>/dev/null; then exec </dev/tty; fi
detetar_sistema
[[ $MODO == lista || $MODO == listar ]] && { listar; exit 0; }

echo "${C_NG}Portal Oracle - instalacao num servidor (sem Docker)${C_0}   (ajuda: $0 --help)"

# ---------------------------------------------------------------------------- escolher a instancia
mapfile -t INSTANCIAS < <(listar_instancias)
if [[ -z $NOME_ARG && -n $MODO && ${#INSTANCIAS[@]} -eq 1 ]]; then
    NOME_ARG=${INSTANCIAS[0]}
elif [[ -z $NOME_ARG && ${#INSTANCIAS[@]} -gt 0 ]]; then
    titulo "Instancias neste servidor"
    OPCOES_INST=(); INST_DEF=""
    for n in "${INSTANCIAS[@]}"; do
        if carregar_estado "$n"; then
            calcular_url
            OPCOES_INST+=("$n|$n  ($DIR, $URL)")
            [[ $(realpath -m "$DIR") == "$(realpath "$SCRIPT_DIR")" ]] && INST_DEF=$n
        else
            OPCOES_INST+=("$n|$n  (sem configuracao guardada)")
        fi
    done
    [[ -z $INST_DEF && ${#INSTANCIAS[@]} -eq 1 ]] && INST_DEF=${INSTANCIAS[0]}
    [[ -z $MODO ]] && OPCOES_INST+=("+nova|Nova instancia")
    escolher NOME_ARG "Qual?" "${INST_DEF:-+nova}" "${OPCOES_INST[@]}"
    [[ $NOME_ARG == +nova ]] && NOME_ARG=""
fi
[[ -z $NOME_ARG && -n $MODO ]] \
    && erro "nao ha instancias registadas (ver: $0 --lista)."

REINSTALAR=0; ESTADO_ORIGEM=""
if [[ -n $NOME_ARG ]] && carregar_estado "$NOME_ARG"; then
    REINSTALAR=1
elif [[ -n $NOME_ARG ]]; then
    NOME=$NOME_ARG
    [[ -f /etc/systemd/system/$NOME.service ]] \
        && aviso "a instancia '$NOME' existe mas nao tem configuracao guardada: as perguntas usam valores por omissao."
fi

[[ $MODO == mostrar ]] && { titulo "Configuracao guardada"; mostrar_estado; exit 0; }
if [[ $MODO == verificar || $MODO == manutencao ]]; then
    (( REINSTALAR )) || erro "a instancia '$NOME_ARG' nao tem configuracao guardada."
    [[ $MODO == verificar ]] && verificar
    titulo "Modo de manutencao de '$NOME'"
    manage maintenance "$MANUT_ACAO" | sed 's/^/   /'
    exit 0
fi
[[ $MODO == desinstalar ]] && desinstalar "$NOME_ARG"

if (( REINSTALAR )); then
    titulo "Configuracao guardada de '$NOME'"
    mostrar_estado
    [[ ${ESTADO_INST:-} == incompleta ]] && aviso "a ultima execucao nao chegou ao fim."
    if [[ -z $MODO ]]; then
        echo
        OPCOES_MENU=("rever|Rever a configuracao (os valores guardados aparecem como omissao)"
                     "reaplicar|Reaplicar sem perguntas, mantendo o codigo instalado")
        [[ -n ${GIT_URL:-} ]] && OPCOES_MENU+=("atualizar|Atualizar o codigo pelo Git ($GIT_BRANCH) e reaplicar sem perguntas")
        OPCOES_MENU+=("desinstalar|Desinstalar esta instancia" "sair|Sair sem alterar nada")
        escolher MODO "O que pretende fazer?" rever "${OPCOES_MENU[@]}"
        [[ $MODO == sair ]] && exit 0
        [[ $MODO == desinstalar ]] && desinstalar "$NOME"
    fi
    [[ $MODO == atualizar && -z ${GIT_URL:-} ]] && erro "a instancia nao tem repositorio Git guardado: use '$0 $NOME' e escolha a origem Git."
    [[ $MODO == reaplicar || $MODO == atualizar ]] && { AUTO=1; titulo "A reaplicar a configuracao guardada (sem perguntas)"; }
elif [[ $MODO == reaplicar || $MODO == atualizar ]]; then
    erro "a instancia '$NOME_ARG' nao tem configuracao guardada: corra '$0 $NOME_ARG'."
fi
(( AUTO )) || echo "Enter aceita o valor entre [ ]. Nada e alterado antes da confirmacao final."

# ---------------------------------------------------------------------------- 1. instancia
titulo "1. Instancia"
if (( REINSTALAR )); then
    info "Instancia: $NOME"
else
    info "O nome identifica o servico systemd, o site do Nginx, a pasta e o utilizador de servico."
    info "Permite ter varias instancias no mesmo servidor (ex. portal-oracle, nome-portal)."
    NOME_DEF=${NOME_ARG:-portal-oracle}
    [[ -z $NOME_ARG ]] && printf '%s\n' "${INSTANCIAS[@]}" | grep -qx "$NOME_DEF" && NOME_DEF=""
    perguntar NOME "Nome da instancia" "$NOME_DEF" '^[a-z][a-z0-9-]{1,30}$'
    if printf '%s\n' "${INSTANCIAS[@]}" | grep -qx "$NOME" && carregar_estado "$NOME"; then
        REINSTALAR=1; aviso "a instancia '$NOME' ja existe: os valores guardados sao usados como omissao."
    fi
fi
[[ -n ${DIR:-} ]] || DIR=$(sed -n 's/^WorkingDirectory=//p' "/etc/systemd/system/$NOME.service" 2>/dev/null || true)
DIR=${DIR:-/opt/$NOME}
(( REINSTALAR )) && info "Os dados (data/), o .env e o .venv sao mantidos."

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
if [[ $MODO == atualizar ]]; then COD_DEF=git
elif [[ $MODO == reaplicar ]]; then tem_codigo "$DIR" && COD_DEF=manter
elif (( REINSTALAR )); then
    tem_codigo "$DIR" && COD_DEF=manter
    [[ -n ${GIT_URL:-} ]] && COD_DEF=git
fi
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
        (( AUTO )) && { AUTO=0; aviso "o certificado guardado tem um problema: indique os ficheiros."; }
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
ABRIR_FW_ANT=${ABRIR_FW:-1}; ABRIR_FW=0
if [[ -n $FW ]]; then
    sim_nao "Firewall ($FW) ativa: abrir as portas do portal?" "$([[ ${ABRIR_FW_ANT:-1} == 1 ]] && echo s || echo n)" && ABRIR_FW=1
fi

# ---------------------------------------------------------------------------- resumo
case $BD_OPCAO in
    manter) BD_RESUMO="manter a atual" ;;
    sqlite) BD_RESUMO="SQLite ($DIR/data/portal.sqlite3)" ;;
    *)      BD_RESUMO="$BD_TIPO $BD_LOCAL - $BD_UTIL@$BD_HOST:$BD_PORTA/$BD_NOME" ;;
esac
calcular_url
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
AUTO=0
sim_nao "Avancar com a instalacao?" s || { echo "Cancelado, nada foi alterado."; exit 0; }
# Grava ja as respostas: se algo falhar a meio, ficam como valores por omissao na proxima execucao
gravar_estado incompleta
info "configuracao guardada em $ESTADO_DIR/$NOME.conf"

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
    manter) ok "codigo mantido"; limpar_docker ;;
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
gravar_estado concluida

# ---------------------------------------------------------------------------- fim
titulo "Concluido"
printf "   %-20s %s\n" \
    "Portal" "$URL  (utilizador $ADMIN_USER)" \
    "Estado / reiniciar" "systemctl status $NOME  |  systemctl restart $NOME" \
    "Logs" "journalctl -u $NOME -f  |  /var/log/nginx/$NOME.*.log" \
    "Base de dados" "$(manage show-storage 2>/dev/null || echo '?')" \
    "Nginx" "$NGX_CONF" \
    "Configuracao" "$ESTADO_DIR/$NOME.conf  (ver: sudo $DIR/instalar.sh --mostrar $NOME)" \
    "Reconfigurar" "sudo $DIR/instalar.sh $NOME" \
    "Remover" "sudo $DIR/instalar.sh --desinstalar $NOME"
[[ ${BD_LOCAL:-} == remota && ${BD_TIPO:-} != sqlite ]] && info "SQL gerado: $DIR/data/bd/$BD_TIPO.sql"
[[ ! -f $DIR/data/admin.json ]] && aviso "ainda nao ha administrador: cd $DIR && sudo -u $UTIL .venv/bin/python manage.py set-admin-password"
echo
info "${C_NG}Faca backup de $DIR/data${C_0} (sobretudo fernet.key e admin.json)."
