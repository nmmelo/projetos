#!/usr/bin/env bash
#
# migrate-php-fpm.sh
# Migração do mod_php (Apache) para php-fpm, virtualhost a virtualhost (Ubuntu).
#
#   - valida os virtualhosts do sites-available / sites-enabled
#   - marca como .delete os que não estão ativos
#   - migra (um a um ou AUTO) só os que estão no sites-enabled:
#       backup do ficheiro, comenta as diretivas php_*, acrescenta o SetHandler
#       do php-fpm em cada <VirtualHost> (80 e 443) e cria o pool em pool.d
#   - valida apache + php-fpm (indica a linha com erro), reload, resumo e log
#   - desativa o mod_php no fim e indica o que falta para o desinstalar
#
# Uso: sudo ./migrate-php-fpm.sh [opções]      (ver --help)
#

set -o pipefail

VERSION="1.0.0"
SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"

# --------------------------------------------------------------- predefinições
# Podem ser alteradas em migrate-php-fpm.conf (na pasta do script) ou por opção.
APACHE_DIR="/etc/apache2"
SITES_AVAILABLE=""            # vazio = $APACHE_DIR/sites-available
SITES_ENABLED=""              # vazio = $APACHE_DIR/sites-enabled
PHP_ETC="/etc/php"
PHP_VER=""                    # vazio = deteção automática (mods-enabled/phpX.Y.load ou php -v)
POOL_DIR=""                   # vazio = $PHP_ETC/<versão>/fpm/pool.d
RUN_DIR="/run/php"            # pasta dos sockets
PHP_LOG_DIR="/var/log"        # error_log = $PHP_LOG_DIR/<nome>.php.error.log
FPM_USER="www-data"
FPM_GROUP="www-data"
PM="dynamic"
PM_MAX_CHILDREN=20
PM_START_SERVERS=4
PM_MIN_SPARE_SERVERS=2
PM_MAX_SPARE_SERVERS=6
PM_MAX_REQUESTS=500
REQUEST_TERMINATE_TIMEOUT=300
CONVERTER_PARA_ADMIN=1        # 1: php_value -> php_admin_value, php_flag -> php_admin_flag
INCLUDE_PATH_PADRAO=1         # 1: acrescenta sempre o include_path padrão
INCLUDE_PATH_TEMPLATE='.:/usr/share/php:{BASE}/conn:{BASE}/codelib:{BASE}/internet/000-config:{BASE}/internet/999-error'
BASE_PADRAO="/var/www"        # {BASE} quando o DocumentRoot não está em /var/<pasta>/
ENV_REGEX='^(ORACLE_|TNS_ADMIN|LD_LIBRARY_PATH|NLS_)'   # variáveis de ambiente propostas para env[...]
LOG_FILE=""                   # vazio = <pasta do script>/logs/migrate-php-fpm_<data>.log
DRY_RUN=0
MODO=""

# Comandos (substituíveis por variáveis de ambiente, útil para testes)
APACHECTL="${APACHECTL:-apache2ctl}"
A2ENMOD="${A2ENMOD:-a2enmod}"
A2DISMOD="${A2DISMOD:-a2dismod}"
A2ENCONF="${A2ENCONF:-a2enconf}"
APT_GET="${APT_GET:-apt-get}"
SERVICE_CMD="${SERVICE_CMD:-}"

CONF_FILE="${CONF_FILE:-$SCRIPT_DIR/migrate-php-fpm.conf}"
# shellcheck source=/dev/null
[[ -f $CONF_FILE ]] && source "$CONF_FILE"

# UTF-8 para os acentos e o alinhamento das tabelas
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) ;;
    *) locale -a 2>/dev/null | grep -qix 'c.utf-\?8' && export LC_ALL=C.UTF-8 ;;
esac

# --------------------------------------------------------------------- cores
if [[ -t 1 ]]; then
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[34m'; C=$'\e[36m'; W=$'\e[1m'; D=$'\e[2m'; N=$'\e[0m'
else
    R=""; G=""; Y=""; B=""; C=""; W=""; D=""; N=""
fi

# ----------------------------------------------------------------- utilitários
log()    { [[ -n $LOG_FILE ]] && printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE"; return 0; }
info()   { printf '%s•%s %s\n' "$C" "$N" "$*"; log "INFO  $*"; }
ok()     { printf '%s✔%s %s\n' "$G" "$N" "$*"; log "OK    $*"; }
warn()   { printf '%s⚠%s %s\n' "$Y" "$N" "$*"; log "AVISO $*"; }
err()    { printf '%s✖%s %s\n' "$R" "$N" "$*"; log "ERRO  $*"; }
titulo() { printf '\n%s%s== %s ==%s\n' "$W" "$B" "$*" "$N"; log "== $* =="; }

# Tabelas: tbl_init "Col1|Col2"; tbl_add "a|b"; tbl_show
TBL_HDR=(); TBL_ROWS=()
tbl_init() { IFS='|' read -r -a TBL_HDR <<<"$1"; TBL_ROWS=(); }
tbl_add()  { TBL_ROWS+=("$1"); }
_pad()     { local s=$1 n=$2; printf '%s%*s' "$s" $(( n - ${#s} )) ''; }
_tbl_print() {
    local -a w=() c=()
    local i r s max=70
    for i in "${!TBL_HDR[@]}"; do w[i]=${#TBL_HDR[i]}; done
    for r in "${TBL_ROWS[@]}"; do
        IFS='|' read -r -a c <<<"$r"
        for i in "${!TBL_HDR[@]}"; do
            s=${c[i]}; (( ${#s} > max )) && s="${s:0:max-1}…"
            (( ${#s} > w[i] )) && w[i]=${#s}
        done
    done
    s="  "; for i in "${!TBL_HDR[@]}"; do s+="$(_pad "${TBL_HDR[i]}" "${w[i]}")  "; done; echo "$s"
    s="  "; for i in "${!TBL_HDR[@]}"; do s+="$(printf '%*s' "${w[i]}" '' | tr ' ' '-')  "; done; echo "$s"
    for r in "${TBL_ROWS[@]}"; do
        IFS='|' read -r -a c <<<"$r"
        s="  "
        for i in "${!TBL_HDR[@]}"; do
            local v=${c[i]}; (( ${#v} > max )) && v="${v:0:max-1}…"
            s+="$(_pad "$v" "${w[i]}")  "
        done
        echo "$s"
    done
}
tbl_show() { local out; out=$(_tbl_print); echo "$out"; [[ -n $LOG_FILE ]] && echo "$out" >>"$LOG_FILE"; return 0; }

# perguntar_sn "Pergunta" s|n  -> 0 = sim
perguntar_sn() {
    local def=${2:-n} a hint="[s/N]"
    [[ $def == s ]] && hint="[S/n]"
    while true; do
        if ! read -r -p "$1 $hint " a; then a=$def; echo; fi
        a=${a:-$def}
        case "${a,,}" in
            s|sim|y|yes) log "PERGUNTA $1 -> sim"; return 0 ;;
            n|nao|não|no) log "PERGUNTA $1 -> não"; return 1 ;;
        esac
    done
}

# perguntar "Texto" "valor por defeito"  -> $RESPOSTA
perguntar() {
    local a
    if ! read -r -p "$1 [${2}]: " a; then a=""; echo; fi
    RESPOSTA=${a:-$2}
    log "PERGUNTA $1 -> $RESPOSTA"
}

# Executa um comando (ou só mostra em simulação). Saída para o ecrã e para o log.
run() {
    log "CMD   $*"
    if (( DRY_RUN )); then printf '  %s[simulação]%s %s\n' "$D" "$N" "$*"; return 0; fi
    local out rc
    out=$("$@" 2>&1); rc=$?
    [[ -n $out ]] && { printf '%s\n' "$out" | sed 's/^/    /'; log "$out"; }
    return $rc
}

servico() {   # servico reload|restart|start|is-active <nome>
    local acao=$1 nome=$2
    if [[ -n $SERVICE_CMD ]]; then $SERVICE_CMD "$acao" "$nome"; return; fi
    if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        if [[ $acao == is-active ]]; then systemctl is-active --quiet "$nome"; else systemctl "$acao" "$nome"; fi
    else
        if [[ $acao == is-active ]]; then service "$nome" status >/dev/null 2>&1; else service "$nome" "$acao"; fi
    fi
}

recarregar() {   # recarregar <serviço>  (reload se ativo, senão start)
    local s=$1
    if (( DRY_RUN )); then info "[simulação] reload $s"; return 0; fi
    if servico is-active "$s"; then
        if run servico reload "$s"; then ok "Reload de $s feito"; else err "Falhou o reload de $s"; return 1; fi
    else
        if run servico start "$s"; then ok "$s iniciado"; else err "Falhou o arranque de $s"; return 1; fi
    fi
}

uso() {
    cat <<EOF
$SCRIPT_NAME $VERSION — migração do mod_php (Apache) para php-fpm, por virtualhost

Uso: sudo ./$SCRIPT_NAME [opções]

Opções:
  -a, --available DIR   pasta sites-available   (defeito: $APACHE_DIR/sites-available)
  -e, --enabled DIR     pasta sites-enabled     (defeito: $APACHE_DIR/sites-enabled)
  -p, --php X.Y         versão do PHP           (defeito: a do mod_php ativo)
  -d, --pool-dir DIR    pasta dos pools         (defeito: $PHP_ETC/X.Y/fpm/pool.d)
  -l, --log FICHEIRO    ficheiro de log         (defeito: <pasta do script>/logs/)
  -m, --modo MODO       sem menu: validar | delete | migrar | auto | ini | testar | desativar
      --manter-tipo     php_value fica php_value (por defeito passa a php_admin_value)
      --dry-run         simulação: mostra tudo e não altera nada
  -h, --help            esta ajuda

As predefinições (pm, user, include_path, ...) podem ser alteradas em:
  $CONF_FILE
EOF
}

# ------------------------------------------------------------------- opções
while [[ $# -gt 0 ]]; do
    case $1 in
        -a|--available) SITES_AVAILABLE=$2; shift 2 ;;
        -e|--enabled)   SITES_ENABLED=$2; shift 2 ;;
        -p|--php)       PHP_VER=$2; shift 2 ;;
        -d|--pool-dir)  POOL_DIR=$2; shift 2 ;;
        -l|--log)       LOG_FILE=$2; shift 2 ;;
        -m|--modo)      MODO=$2; shift 2 ;;
        --manter-tipo)  CONVERTER_PARA_ADMIN=0; shift ;;
        --dry-run)      DRY_RUN=1; shift ;;
        -h|--help)      uso; exit 0 ;;
        *) echo "Opção desconhecida: $1"; uso; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    printf '%s✖ Este script tem de ser executado como root.%s\n' "$R" "$N"
    printf '  Use: %ssudo %s/%s%s\n' "$W" "$SCRIPT_DIR" "$SCRIPT_NAME" "$N"
    exit 1
fi

SITES_AVAILABLE=${SITES_AVAILABLE:-$APACHE_DIR/sites-available}
SITES_ENABLED=${SITES_ENABLED:-$APACHE_DIR/sites-enabled}
SITES_AVAILABLE=${SITES_AVAILABLE%/}
SITES_ENABLED=${SITES_ENABLED%/}
if [[ -z $LOG_FILE ]]; then
    mkdir -p "$SCRIPT_DIR/logs"
    LOG_FILE="$SCRIPT_DIR/logs/migrate-php-fpm_$TS.log"
fi
: >>"$LOG_FILE" || { echo "Não é possível escrever no log $LOG_FILE"; exit 1; }
log "===== $SCRIPT_NAME $VERSION — início (dry-run=$DRY_RUN) ====="

for d in "$SITES_AVAILABLE" "$SITES_ENABLED"; do
    [[ -d $d ]] || { err "Pasta não encontrada: $d"; exit 1; }
done

# ---------------------------------------------------------------- deteção PHP
detetar_php() {
    local f
    if [[ -z $PHP_VER ]]; then
        for f in "$APACHE_DIR"/mods-enabled/php*.load; do
            [[ -e $f ]] || continue
            f=$(basename "$f" .load); PHP_VER=${f#php}
        done
    fi
    if [[ -z $PHP_VER ]] && command -v php >/dev/null 2>&1; then
        PHP_VER=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null)
    fi
    if [[ ! $PHP_VER =~ ^[0-9]+\.[0-9]+$ ]]; then
        err "Não foi possível detetar a versão do PHP. Indique-a com --php X.Y"
        exit 1
    fi
    MOD_PHP="php$PHP_VER"
    FPM_SERVICE="php$PHP_VER-fpm"
    FPM_BIN="${FPM_BIN:-php-fpm$PHP_VER}"
    POOL_DIR=${POOL_DIR:-$PHP_ETC/$PHP_VER/fpm/pool.d}
    POOL_DIR=${POOL_DIR%/}
}

mod_php_ativo()   { [[ -e $APACHE_DIR/mods-enabled/$MOD_PHP.load ]]; }
fpm_instalado()   { command -v "$FPM_BIN" >/dev/null 2>&1; }
modulo_ativo()    { [[ -e $APACHE_DIR/mods-enabled/$1.load ]]; }

# ------------------------------------------------------------- validações
mostrar_linha() {   # mostrar_linha ficheiro nº
    local f=$1 n=$2
    [[ -r $f ]] || return 0
    printf '    %s→ %s, linha %s:%s\n' "$Y" "$f" "$n" "$N"
    awk -v n="$n" 'NR>=n-2 && NR<=n+2 { printf "      %s %5d | %s\n", (NR==n ? ">>" : "  "), NR, $0 }' "$f"
    log "LINHA $f:$n: $(sed -n "${n}p" "$f")"
}

validar_apache() {
    local out rc l
    command -v "$APACHECTL" >/dev/null 2>&1 || { err "$APACHECTL não encontrado"; return 1; }
    out=$($APACHECTL configtest 2>&1); rc=$?
    log "apache configtest (rc=$rc): $out"
    if (( rc == 0 )); then ok "Apache: sintaxe OK"; return 0; fi
    err "Apache: erro na configuração"
    printf '%s\n' "$out" | sed 's/^/    /'
    while IFS= read -r l; do
        [[ $l =~ line\ ([0-9]+)\ of\ ([^:]+) ]] && mostrar_linha "${BASH_REMATCH[2]}" "${BASH_REMATCH[1]}"
    done <<<"$out"
    return 1
}

validar_fpm() {
    local out rc l
    fpm_instalado || { warn "php-fpm $PHP_VER não está instalado — validação ignorada"; return 0; }
    out=$("$FPM_BIN" -t 2>&1); rc=$?
    log "php-fpm -t (rc=$rc): $out"
    if (( rc == 0 )); then ok "php-fpm $PHP_VER: sintaxe OK"; return 0; fi
    err "php-fpm $PHP_VER: erro na configuração"
    printf '%s\n' "$out" | sed 's/^/    /'
    while IFS= read -r l; do
        [[ $l =~ \[([^]:]+):([0-9]+)\] ]] && mostrar_linha "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    done <<<"$out"
    return 1
}

validar_tudo() {
    titulo "Validação das configurações"
    local r=0
    validar_apache || r=1
    validar_fpm || r=1
    return $r
}

# ---------------------------------------------------------- pré-requisitos
verificar_requisitos() {
    titulo "Pré-requisitos"
    if ! command -v "$APACHECTL" >/dev/null 2>&1; then
        err "Apache ($APACHECTL) não encontrado."; return 1
    fi
    ok "Apache encontrado"
    if mod_php_ativo; then ok "mod_php ativo: $MOD_PHP"; else warn "mod_php ($MOD_PHP) não está ativo no Apache"; fi

    if fpm_instalado; then
        ok "php-fpm $PHP_VER instalado ($FPM_BIN)"
    else
        warn "php-fpm $PHP_VER NÃO está instalado."
        if perguntar_sn "Instalar php$PHP_VER-fpm (mesma versão do PHP)?" s; then
            run "$APT_GET" install -y "php$PHP_VER-fpm" || { err "Falhou a instalação"; return 1; }
            hash -r
            fpm_instalado && ok "php-fpm $PHP_VER instalado" || { (( DRY_RUN )) || { err "php-fpm continua sem estar disponível"; return 1; }; }
        else
            (( DRY_RUN )) || { err "Sem php-fpm não é possível migrar."; return 1; }
        fi
    fi

    local m falta=()
    for m in proxy proxy_fcgi setenvif; do modulo_ativo "$m" || falta+=("$m"); done
    if (( ${#falta[@]} )); then
        warn "Módulos do Apache necessários e não ativos: ${falta[*]}"
        if perguntar_sn "Ativar com a2enmod ${falta[*]}?" s; then
            run "$A2ENMOD" "${falta[@]}" || warn "a2enmod devolveu erro"
        fi
    else
        ok "Módulos proxy, proxy_fcgi e setenvif ativos"
    fi

    if [[ -e $APACHE_DIR/conf-enabled/php$PHP_VER-fpm.conf ]]; then
        info "conf php$PHP_VER-fpm ativa: os vhosts não migrados usam o pool por defeito (www) quando o mod_php for desativado"
    fi

    if [[ ! -d $POOL_DIR ]]; then
        warn "A pasta dos pools não existe: $POOL_DIR"
        if perguntar_sn "Criar $POOL_DIR?" s; then run mkdir -p "$POOL_DIR"; else return 1; fi
    fi
    local fpmconf="$PHP_ETC/$PHP_VER/fpm/php-fpm.conf"
    if [[ -r $fpmconf ]] && ! grep -Eq "^[[:space:]]*include[[:space:]]*=[[:space:]]*${POOL_DIR//./\\.}/" "$fpmconf"; then
        warn "O $fpmconf não inclui $POOL_DIR — os pools criados aí não serão carregados (verifique o include=)"
    fi

    carregar_envvars
    return 0
}

# Variáveis de ambiente exportadas no /etc/apache2/envvars (ex. Oracle)
ENVVARS_NOME=(); ENVVARS_VALOR=()
carregar_envvars() {
    ENVVARS_NOME=(); ENVVARS_VALOR=()
    local f="$APACHE_DIR/envvars" l n v
    [[ -r $f ]] || return 0
    while IFS= read -r l; do
        [[ $l =~ ^[[:space:]]*export[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
        n=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        [[ $n =~ $ENV_REGEX ]] || continue
        v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
        # remove referências a outras variáveis ($LD_LIBRARY_PATH, ${X}) que o php-fpm não expande
        v=$(sed -E 's/:?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?//g; s/^://' <<<"$v")
        ENVVARS_NOME+=("$n"); ENVVARS_VALOR+=("$v")
    done <"$f"
    if (( ${#ENVVARS_NOME[@]} )); then
        info "Variáveis de ambiente no $f a propor nos pools: ${ENVVARS_NOME[*]}"
        if [[ " ${ENVVARS_NOME[*]} " == *" LD_LIBRARY_PATH "* ]]; then
            warn "LD_LIBRARY_PATH: o env[] do pool não chega para o processo master carregar o oci8."
            warn "  Garanta /etc/ld.so.conf.d/oracle.conf + ldconfig, ou um override systemd do $FPM_SERVICE."
        fi
    fi
}

# ----------------------------------------------------- php.ini e extensões
comparar_ini() {
    titulo "php.ini e extensões: apache2 vs fpm (PHP $PHP_VER)"
    local ia="$PHP_ETC/$PHP_VER/apache2/php.ini" if_="$PHP_ETC/$PHP_VER/fpm/php.ini"
    if [[ ! -r $ia || ! -r $if_ ]]; then warn "Não encontrei $ia e/ou $if_"; return 0; fi
    local ta tf
    ta=$(mktemp); tf=$(mktemp)
    _ini_norm() { grep -Ev '^[[:space:]]*(;|\[|$)' "$1" | sed -E 's/[[:space:]]*=[[:space:]]*/=/; s/[[:space:]]+$//' | sort -u; }
    _ini_norm "$ia" >"$ta"; _ini_norm "$if_" >"$tf"
    local -A va=() vf=() k=()
    local l
    while IFS= read -r l; do va[${l%%=*}]=${l#*=}; k[${l%%=*}]=1; done <"$ta"
    while IFS= read -r l; do vf[${l%%=*}]=${l#*=}; k[${l%%=*}]=1; done <"$tf"
    rm -f "$ta" "$tf"
    tbl_init "Diretiva|apache2/php.ini|fpm/php.ini"
    local n dif=0
    for n in $(printf '%s\n' "${!k[@]}" | sort); do
        [[ ${va[$n]-} == "${vf[$n]-}" ]] && continue
        tbl_add "$n|${va[$n]-(não definido)}|${vf[$n]-(não definido)}"; dif=1
    done
    if (( dif )); then
        warn "Diferenças entre os php.ini (com o php-fpm passa a valer o fpm/php.ini):"
        tbl_show
        if perguntar_sn "Copiar o apache2/php.ini para o fpm/php.ini (com backup)?" n; then
            run cp -p "$if_" "$if_.bak-$TS" && run cp "$ia" "$if_" && ok "fpm/php.ini substituído (backup $if_.bak-$TS)"
        fi
    else
        ok "Os dois php.ini têm os mesmos valores"
    fi

    local da="$PHP_ETC/$PHP_VER/apache2/conf.d" df="$PHP_ETC/$PHP_VER/fpm/conf.d" e falta=()
    if [[ -d $da && -d $df ]]; then
        for e in "$da"/*.ini; do
            [[ -e $e ]] || continue
            [[ -e $df/$(basename "$e") ]] || falta+=("$(basename "$e")")
        done
        if (( ${#falta[@]} )); then
            warn "Extensões ativas no apache2 e não no fpm: ${falta[*]}"
            if perguntar_sn "Ativar estas extensões no fpm (mesmo link do apache2)?" s; then
                for e in "${falta[@]}"; do
                    if [[ -L $da/$e ]]; then run ln -s "$(readlink "$da/$e")" "$df/$e"; else run cp -p "$da/$e" "$df/$e"; fi
                done
                ok "Extensões ativadas no fpm (é preciso reload do $FPM_SERVICE)"
            fi
        else
            ok "As mesmas extensões estão ativas no apache2 e no fpm"
        fi
    fi
}

# ------------------------------------------------------------ sites
AV_LIST=(); EN_LIST=(); declare -A EN_MAP=(); EN_ALVO=()
carregar_sites() {
    AV_LIST=(); EN_LIST=(); EN_ALVO=(); EN_MAP=()
    local f alvo avr
    avr=$(readlink -f "$SITES_AVAILABLE")
    for f in "$SITES_AVAILABLE"/*; do
        [[ -f $f ]] || continue
        case $f in *.bak-*|*.delete|*~) continue ;; esac
        AV_LIST+=("$(basename "$f")")
    done
    for f in "$SITES_ENABLED"/*; do
        [[ -e $f || -L $f ]] || continue
        EN_LIST+=("$(basename "$f")")
        alvo=$(readlink -f "$f")
        EN_ALVO+=("$alvo")
        [[ $(dirname "$alvo") == "$avr" ]] && EN_MAP[$(basename "$alvo")]=$(basename "$f")
    done
}

# analisar_vhost ficheiro -> arrays B_* (blocos), P_* (php_*), E_* (SetEnv)
analisar_vhost() {
    B_START=(); B_END=(); B_ADDR=(); B_SN=(); B_SA=(); B_DR=(); B_FPM=()
    P_LINE=(); P_BLOCK=(); P_DIR=(); P_NAME=(); P_VAL=()
    E_LINE=(); E_BLOCK=(); E_NAME=(); E_VAL=()
    local tag a b c d rest
    while IFS='|' read -r tag a b c d rest; do
        case $tag in
            BS)  B_START[a]=$b; B_ADDR[a]=$c; B_FPM[a]=0 ;;
            BE)  B_END[a]=$b ;;
            SN)  B_SN[a]=$b ;;
            SA)  B_SA[a]="${B_SA[a]:+${B_SA[a]} }$b" ;;
            DR)  B_DR[a]=$b ;;
            PHP) P_LINE+=("$a"); P_BLOCK+=("$b"); P_DIR+=("$c"); P_NAME+=("$d"); P_VAL+=("$rest") ;;
            ENV) E_LINE+=("$a"); E_BLOCK+=("$b"); E_NAME+=("$c"); E_VAL+=("$d${rest:+|$rest}") ;;
            FPM) B_FPM[b]=1 ;;
        esac
    done < <(awk '
        function trim(s) { gsub(/^[ \t]+|[ \t\r]+$/, "", s); return s }
        { t = trim($0); lt = tolower(t) }
        t == "" || substr(t, 1, 1) == "#" { next }
        lt ~ /^<virtualhost[ \t>]/ {
            b++; addr = t; sub(/^<[^ \t>]+[ \t]*/, "", addr); sub(/>.*$/, "", addr)
            inb = 1; print "BS|" b "|" NR "|" addr; next }
        lt ~ /^<\/virtualhost>/ { if (inb) print "BE|" b "|" NR; inb = 0; next }
        lt ~ /^servername[ \t]/   { if (inb) { split(t, a, /[ \t]+/); print "SN|" b "|" a[2] }; next }
        lt ~ /^serveralias[ \t]/  { if (inb) { v = t; sub(/^[^ \t]+[ \t]+/, "", v); print "SA|" b "|" v }; next }
        lt ~ /^documentroot[ \t]/ { if (inb) { v = t; sub(/^[^ \t]+[ \t]+/, "", v); gsub(/"/, "", v); sub(/\/$/, "", v); print "DR|" b "|" v }; next }
        lt ~ /^php_(admin_)?(value|flag)[ \t]/ {
            split(t, a, /[ \t]+/); v = t; sub(/^[^ \t]+[ \t]+[^ \t]+[ \t]*/, "", v)
            print "PHP|" NR "|" (inb ? b : 0) "|" tolower(a[1]) "|" a[2] "|" v; next }
        lt ~ /^setenv[ \t]/ {
            split(t, a, /[ \t]+/); v = t; sub(/^[^ \t]+[ \t]+[^ \t]+[ \t]*/, "", v)
            print "ENV|" NR "|" (inb ? b : 0) "|" a[2] "|" v; next }
        lt ~ /sethandler/ && lt ~ /proxy:unix:/ { print "FPM|" NR "|" (inb ? b : 0) "|x"; next }
    ' "$1")
}

# Agrupa os blocos <VirtualHost> pelo ServerName (80 + 443 = um pool)
GRUPOS=(); declare -A G_BLOCOS=()
agrupar() {
    GRUPOS=(); G_BLOCOS=()
    local b k
    for b in "${!B_START[@]}"; do
        k=${B_SN[b]:-${B_SA[b]%% *}}; k=${k:-"(sem ServerName)"}
        [[ -z ${G_BLOCOS[$k]+x} ]] && { GRUPOS+=("$k"); G_BLOCOS[$k]=""; }
        G_BLOCOS[$k]+="$b "
    done
}

estado_vhost() {   # depois de analisar_vhost: devolve texto do estado
    local b fpm=0 dr=0
    for b in "${!B_START[@]}"; do
        [[ ${B_FPM[b]} == 1 ]] && fpm=1
        [[ -n ${B_DR[b]} ]] && dr=1
    done
    if (( fpm )); then echo "php-fpm"; elif (( ${#P_LINE[@]} )); then echo "mod_php (diretivas)"
    elif (( dr )); then echo "mod_php"; elif (( ${#B_START[@]} == 0 )); then echo "sem VirtualHost"
    else echo "sem DocumentRoot"; fi
}

# ---------------------------------------------------------- 1) validar
validar_vhosts() {
    titulo "VirtualHosts: sites-available vs sites-enabled"
    carregar_sites
    info "sites-available: $SITES_AVAILABLE (${#AV_LIST[@]} ficheiros)"
    info "sites-enabled:   $SITES_ENABLED (${#EN_LIST[@]} entradas)"
    tbl_init "Ficheiro|Enabled|ServerName|Portas|DocumentRoot|php_*|Estado"
    local f b sn portas dr en dr_ok
    for f in "${AV_LIST[@]}"; do
        analisar_vhost "$SITES_AVAILABLE/$f"
        sn=""; portas=""; dr=""
        for b in "${!B_START[@]}"; do
            [[ -z $sn ]] && sn=${B_SN[b]}
            portas+="${B_ADDR[b]##*:} "
            [[ -z $dr ]] && dr=${B_DR[b]}
        done
        en=${EN_MAP[$f]:+sim}; en=${en:-NÃO}
        dr_ok=$dr; [[ -n $dr && ! -d $dr ]] && dr_ok="$dr (não existe!)"
        tbl_add "$f|$en|${sn:--}|${portas% }|${dr_ok:--}|${#P_LINE[@]}|$(estado_vhost)"
    done
    tbl_show
    local i outros=0
    for i in "${!EN_LIST[@]}"; do
        local alvo=${EN_ALVO[i]}
        if [[ ! -e $alvo ]]; then
            warn "sites-enabled/${EN_LIST[i]}: link quebrado (→ $alvo)"; outros=1
        elif [[ $(dirname "$alvo") != "$(readlink -f "$SITES_AVAILABLE")" ]]; then
            warn "sites-enabled/${EN_LIST[i]} não aponta para o sites-available (→ $alvo)"; outros=1
        fi
    done
    (( outros )) || ok "Todas as entradas do sites-enabled apontam para o sites-available"
    local nao=0
    for f in "${AV_LIST[@]}"; do [[ -z ${EN_MAP[$f]} ]] && ((nao++)); done
    (( nao )) && info "$nao ficheiro(s) no sites-available sem estar no sites-enabled (opção 2 para marcar como .delete)"
}

# ---------------------------------------------------------- 2) .delete
marcar_delete() {
    titulo "Não usados (sites-available sem sites-enabled)"
    carregar_sites
    local f lista=() existentes=()
    for f in "${AV_LIST[@]}"; do [[ -z ${EN_MAP[$f]} ]] && lista+=("$f"); done
    for f in "$SITES_AVAILABLE"/*.delete; do [[ -e $f ]] && existentes+=("$(basename "$f")"); done
    if (( ${#existentes[@]} )); then
        info "Já marcados como .delete:"
        tbl_init "Ficheiro"; for f in "${existentes[@]}"; do tbl_add "$f"; done; tbl_show
    fi
    if (( ${#lista[@]} == 0 )); then ok "Não há ficheiros por marcar"; return 0; fi
    tbl_init "Ficheiro|ServerName|DocumentRoot"
    local b sn dr
    for f in "${lista[@]}"; do
        analisar_vhost "$SITES_AVAILABLE/$f"; sn=""; dr=""
        for b in "${!B_START[@]}"; do [[ -z $sn ]] && sn=${B_SN[b]}; [[ -z $dr ]] && dr=${B_DR[b]}; done
        tbl_add "$f|${sn:--}|${dr:--}"
    done
    tbl_show
    local op
    read -r -p "Marcar como .delete: t=todos | u=um a um | n=nenhum [n]: " op || op=n
    case "${op,,}" in
        t) for f in "${lista[@]}"; do _marcar "$f"; done ;;
        u) for f in "${lista[@]}"; do perguntar_sn "Marcar $f como $f.delete?" n && _marcar "$f"; done ;;
        *) info "Nada alterado" ;;
    esac
}
_marcar() {
    local f="$SITES_AVAILABLE/$1"
    if [[ -e $f.delete ]]; then warn "$1.delete já existe — ignorado"; return; fi
    run mv "$f" "$f.delete" && ok "$1 → $1.delete"
    RES_FICH+=("$1"); RES_SITE+=("-"); RES_POOL+=("-"); RES_ESTADO+=("marcado .delete"); RES_NOTA+=("$1.delete")
}

# ---------------------------------------------------------- 3/4) migração
RES_FICH=(); RES_SITE=(); RES_POOL=(); RES_ESTADO=(); RES_NOTA=()
declare -A NOMES_USADOS=()
POOL_ORD=(); declare -A POOL_LINHA=() POOL_COM=()

resumo_add() { RES_FICH+=("$1"); RES_SITE+=("$2"); RES_POOL+=("$3"); RES_ESTADO+=("$4"); RES_NOTA+=("$5"); log "RESUMO $1 | $2 | $3 | $4 | $5"; }

pool_set() {   # pool_set chave linha comentário
    [[ -z ${POOL_LINHA[$1]+x} ]] && POOL_ORD+=("$1")
    POOL_LINHA[$1]=$2; POOL_COM[$1]=$3
}

nome_curto() {
    local n=${1,,}
    n=${n#www.}; n=${n%%.*}; n=${n//[^a-z0-9_-]/-}
    echo "${n:-site}"
}

nome_em_uso() {   # nome
    local sock="$RUN_DIR/php$PHP_VER-fpm-$1.sock"
    [[ -n ${NOMES_USADOS[$1]} ]] && return 0
    [[ -e $POOL_DIR/$1.conf ]] && return 0
    grep -qsF -- "$sock" "$POOL_DIR"/*.conf && return 0
    return 1
}

base_de_docroot() {
    if [[ $1 =~ ^/var/([^/]+) ]]; then echo "/var/${BASH_REMATCH[1]}"; else echo "$BASE_PADRAO"; fi
}

linha_fpm() {   # diretiva nome valor -> linha no formato php-fpm
    local dir=$1 n=$2 v=$3 tipo
    case $dir in
        php_value)       tipo=$( ((CONVERTER_PARA_ADMIN)) && echo php_admin_value || echo php_value ) ;;
        php_flag)        tipo=$( ((CONVERTER_PARA_ADMIN)) && echo php_admin_flag  || echo php_flag ) ;;
        *)               tipo=$dir ;;
    esac
    [[ $tipo == *flag ]] && v=${v,,}
    [[ -z $v ]] && v='""'
    echo "$tipo[$n] = $v"
}

# Altera o vhost: comenta as linhas indicadas e insere o bloco do handler antes de </VirtualHost>
gerar_vhost() {   # origem destino "linhas a comentar" "linhas </VirtualHost>" "bloco"
    BLOCO_HANDLER="$5" awk -v cl=" $3 " -v il=" $4 " '
        index(il, " " NR " ") > 0 {
            ind = $0; sub(/[^ \t].*$/, "", ind)
            n = split(ENVIRON["BLOCO_HANDLER"], h, "\n")
            for (i = 1; i <= n; i++) print ind "    " h[i]
        }
        index(cl, " " NR " ") > 0 {
            m = match($0, /[^ \t]/); print substr($0, 1, m - 1) "#MIGRADO-FPM# " substr($0, m); next
        }
        { print }' "$1" >"$2"
}

# .htaccess com php_value/php_flag -> .user.ini (lido pelo php-fpm, mesma pasta)
HT_LISTA=()
procurar_htaccess() {   # docroot
    HT_LISTA=()
    [[ -n $1 && -d $1 ]] || return 0
    mapfile -t HT_LISTA < <(find "$1" -xdev -type f -name .htaccess -exec \
        grep -lEi '^[[:space:]]*php_(admin_)?(value|flag)[[:space:]]' {} + 2>/dev/null)
}
converter_htaccess() {   # ficheiro .htaccess (aplica)
    local ht=$1 dir ui
    dir=$(dirname "$ht"); ui="$dir/.user.ini"
    run cp -p "$ht" "$ht.bak-$TS" || return 1
    [[ -e $ui ]] && run cp -p "$ui" "$ui.bak-$TS"
    if (( ! DRY_RUN )); then
        {
            echo "; migrate-php-fpm $TS: convertido de $ht"
            awk '{ t = $0; sub(/^[ \t]+/, "", t) }
                 tolower(t) ~ /^php_(admin_)?(value|flag)[ \t]/ {
                    split(t, a, /[ \t]+/); v = t; sub(/^[^ \t]+[ \t]+[^ \t]+[ \t]*/, "", v)
                    print a[2] " = " v }' "$ht"
        } >>"$ui"
        chown --reference="$ht" "$ui" 2>/dev/null; chmod 644 "$ui"
        sed -i -E 's/^([[:space:]]*)(php_(admin_)?(value|flag)[[:space:]])/\1#MIGRADO-FPM# \2/I' "$ht"
    fi
    log "HTACCESS $ht -> $ui"
    ok ".htaccess convertido: $ht → $ui"
}

# Migra um grupo (ServerName) de um ficheiro. Devolve 2 para abortar tudo.
migrar_grupo() {   # ficheiro entrada_enabled chave primeiro(1/0)
    local f=$1 en=$2 key=$3 primeiro=$4
    local fb; fb=$(basename "$f")
    analisar_vhost "$f"; agrupar
    local blocos=${G_BLOCOS[$key]}
    [[ -z $blocos ]] && { warn "Grupo $key não encontrado em $f"; return 0; }

    local b sn="" sas="" dr="" portas="" fpm=0 fins=""
    for b in $blocos; do
        [[ -z $sn ]] && sn=${B_SN[b]}
        [[ -n ${B_SA[b]} ]] && sas+="${B_SA[b]} "
        [[ -z $dr ]] && dr=${B_DR[b]}
        portas+="${B_ADDR[b]} "
        [[ ${B_FPM[b]} == 1 ]] && fpm=1
        [[ -n ${B_END[b]} ]] && fins+="${B_END[b]} "
    done
    sas=$(tr ' ' '\n' <<<"$sas" | awk 'NF && !s[$0]++' | xargs)
    local rotulo=${sn:-$key}

    titulo "VirtualHost $rotulo"
    tbl_init "Campo|Valor"
    tbl_add "Ficheiro|$f"
    tbl_add "sites-enabled|$SITES_ENABLED/$en"
    tbl_add "ServerName|${sn:--}"
    tbl_add "ServerAlias|${sas:--}"
    tbl_add "VirtualHost|${portas% }"
    tbl_add "DocumentRoot|${dr:--}$([[ -n $dr && ! -d $dr ]] && echo ' (não existe neste servidor!)')"
    tbl_show

    if (( fpm )); then
        ok "Já tem SetHandler para o php-fpm — já migrado."
        resumo_add "$fb" "$rotulo" "-" "já migrado" ""
        return 0
    fi

    # diretivas php_* deste grupo (+ as que estão fora de <VirtualHost>, no primeiro grupo)
    local i pidx=() eidx=()
    for i in "${!P_LINE[@]}"; do
        if [[ " $blocos " == *" ${P_BLOCK[i]} "* ]] || { [[ ${P_BLOCK[i]} == 0 ]] && (( primeiro )); }; then pidx+=("$i"); fi
    done
    for i in "${!E_LINE[@]}"; do
        [[ " $blocos " == *" ${E_BLOCK[i]} "* ]] && eidx+=("$i")
    done

    if [[ -z $dr ]] && (( ${#pidx[@]} == 0 )); then
        info "Sem DocumentRoot nem diretivas PHP (redirect/proxy?) — não precisa de php-fpm."
        if (( AUTO )) || ! perguntar_sn "Migrar mesmo assim?" n; then
            resumo_add "$fb" "$rotulo" "-" "ignorado" "sem DocumentRoot"; return 0
        fi
    fi

    if (( ! AUTO )); then
        local op
        while true; do
            read -r -p "Existe no sites-enabled o portal ${W}$rotulo${N}. Migrar para php-fpm? [s/N/a=abortar] " op || op=a
            log "PERGUNTA migrar $rotulo -> ${op:-n}"
            case "${op,,}" in
                s|sim) break ;;
                ""|n|nao|não) resumo_add "$fb" "$rotulo" "-" "ignorado" "pelo utilizador"; return 0 ;;
                a) return 2 ;;
            esac
        done
    fi

    # ---- nome do pool (único)
    local nome sec=$rotulo k=2
    nome=$(nome_curto "$rotulo")
    if nome_em_uso "$nome"; then
        warn "O nome '$nome' já está em uso (pool.d ou socket)."
        local base=$nome dom
        dom=$(cut -d. -f2 <<<"${rotulo,,}"); [[ ${rotulo,,} == www.* ]] && dom=$(cut -d. -f3 <<<"${rotulo,,}")
        dom=${dom//[^a-z0-9_-]/-}
        nome="$base-$dom"; [[ -z $dom || $dom == "$base" ]] && nome="$base-2"
        while nome_em_uso "$nome"; do nome="$base-$k"; ((k++)); done
    fi
    if (( ! AUTO )); then
        while true; do
            perguntar "Nome do pool (ficheiro $POOL_DIR/<nome>.conf)" "$nome"
            if [[ ! $RESPOSTA =~ ^[A-Za-z0-9._-]+$ ]]; then warn "Nome inválido (use letras, números, . _ -)"; continue; fi
            if [[ $RESPOSTA != "$nome" ]] && nome_em_uso "$RESPOSTA"; then warn "'$RESPOSTA' já existe — escolha outro"; continue; fi
            nome=$RESPOSTA; break
        done
    fi
    grep -qsxF -- "[$sec]" "$POOL_DIR"/*.conf && sec=$nome
    local sock="$RUN_DIR/php$PHP_VER-fpm-$nome.sock"
    local errlog="$PHP_LOG_DIR/$nome.php.error.log"
    local pool_file="$POOL_DIR/$nome.conf"

    # ---- parâmetros do pool
    POOL_ORD=(); POOL_LINHA=(); POOL_COM=()
    pool_set log_errors "php_admin_flag[log_errors] = on" "padrão"
    pool_set error_log  "php_admin_value[error_log] = $errlog" "padrão"
    local base_inc std_inc=""
    if (( INCLUDE_PATH_PADRAO )); then
        base_inc=$(base_de_docroot "$dr")
        std_inc=${INCLUDE_PATH_TEMPLATE//\{BASE\}/$base_inc}
        pool_set include_path "php_admin_value[include_path] = \"$std_inc\"" "padrão (base $base_inc, do DocumentRoot)"
    fi

    local comentar="" linha op novo
    if (( ${#pidx[@]} )); then
        echo
        info "Diretivas do mod_php a retirar do vhost (dão erro sem o mod_php) e a passar para o pool:"
        tbl_init "Linha|Apache (mod_php)|php-fpm (pool)"
        for i in "${pidx[@]}"; do
            tbl_add "${P_LINE[i]}|${P_DIR[i]} ${P_NAME[i]} ${P_VAL[i]}|$(linha_fpm "${P_DIR[i]}" "${P_NAME[i]}" "${P_VAL[i]}")"
        done
        tbl_show
    fi
    for i in "${pidx[@]}"; do
        comentar+="${P_LINE[i]} "
        linha=$(linha_fpm "${P_DIR[i]}" "${P_NAME[i]}" "${P_VAL[i]}")
        local orig="${P_DIR[i]} ${P_NAME[i]} ${P_VAL[i]}"
        if (( ! AUTO )); then
            echo
            printf '  %s[linha %s]%s %s\n' "$D" "${P_LINE[i]}" "$N" "$orig"
            printf '      → %s%s%s\n' "$G" "$linha" "$N"
            local extra=""
            if [[ ${P_NAME[i]} == include_path && -n $std_inc ]]; then
                printf '      padrão: php_admin_value[include_path] = "%s"\n' "$std_inc"
                extra=" | p=usar o padrão"
            fi
            while true; do
                read -r -p "      Enter=manter igual | e=editar | r=remover$extra | a=abortar: " op || op=""
                case "${op,,}" in
                    "") pool_set "${P_NAME[i]}" "$linha" "Apache linha ${P_LINE[i]}: $orig"; break ;;
                    p)  [[ -n $extra ]] && { pool_set include_path "php_admin_value[include_path] = \"$std_inc\"" "padrão"; break; } ;;
                    e)  perguntar "      Novo valor de ${P_NAME[i]}" "${P_VAL[i]}"; novo=$RESPOSTA
                        pool_set "${P_NAME[i]}" "$(linha_fpm "${P_DIR[i]}" "${P_NAME[i]}" "$novo")" "Apache linha ${P_LINE[i]} (editado): $orig"; break ;;
                    r)  [[ ${P_NAME[i]} == include_path ]] && unset 'POOL_LINHA[include_path]'
                        info "      ${P_NAME[i]} não passa para o pool"; break ;;
                    a)  return 2 ;;
                esac
            done
            log "PARAM ${P_NAME[i]} -> ${op:-manter}"
        else
            pool_set "${P_NAME[i]}" "$linha" "Apache linha ${P_LINE[i]}: $orig"
        fi
    done

    # ---- variáveis de ambiente (SetEnv do vhost + envvars do Apache)
    local en_n en_v def
    local -a cand_n=() cand_v=() cand_o=()
    for i in "${eidx[@]}"; do cand_n+=("${E_NAME[i]}"); cand_v+=("${E_VAL[i]}"); cand_o+=("SetEnv linha ${E_LINE[i]}"); done
    for i in "${!ENVVARS_NOME[@]}"; do cand_n+=("${ENVVARS_NOME[i]}"); cand_v+=("${ENVVARS_VALOR[i]}"); cand_o+=("$APACHE_DIR/envvars"); done
    for i in "${!cand_n[@]}"; do
        en_n=${cand_n[i]}; en_v=${cand_v[i]}; en_v=${en_v%\"}; en_v=${en_v#\"}
        [[ -n ${POOL_LINHA[env:$en_n]+x} ]] && continue
        def=n; [[ $en_n =~ $ENV_REGEX ]] && def=s
        if (( AUTO )); then
            [[ $def == s ]] && pool_set "env:$en_n" "env[$en_n] = \"$en_v\"" "${cand_o[i]}"
        else
            echo
            printf '  Variável de ambiente (%s): env[%s] = "%s"\n' "${cand_o[i]}" "$en_n" "$en_v"
            perguntar_sn "      Colocar no pool?" "$def" && pool_set "env:$en_n" "env[$en_n] = \"$en_v\"" "${cand_o[i]}"
        fi
    done

    # ---- .htaccess com php_value (dão erro 500 sem o mod_php)
    local ht_conv=0 ht
    procurar_htaccess "$dr"
    if (( ${#HT_LISTA[@]} )); then
        echo
        warn ".htaccess com diretivas php_* no DocumentRoot (dão erro 500 sem o mod_php):"
        tbl_init ".htaccess|Diretivas"
        for ht in "${HT_LISTA[@]}"; do
            tbl_add "$ht|$(grep -Eic '^[[:space:]]*php_(admin_)?(value|flag)[[:space:]]' "$ht")"
        done
        tbl_show
        if (( AUTO )) || perguntar_sn "Converter para .user.ini na mesma pasta (o php-fpm lê-o, mantém o efeito por pasta)?" s; then
            ht_conv=1
        fi
    fi

    # ---- gerar o novo vhost e o pool (temporários) e mostrar
    local bloco
    bloco="# migrate-php-fpm $TS: PHP pelo php-fpm (pool $nome)
<FilesMatch \".+\\.ph(ar|p|tml)\$\">
    SetHandler \"proxy:unix:$sock|fcgi://localhost/\"
</FilesMatch>
<Files \".user.ini\">
    Require all denied
</Files>"
    local tv tp
    tv=$(mktemp); tp=$(mktemp)
    gerar_vhost "$f" "$tv" "$comentar" "$fins" "$bloco"
    {
        echo "; Pool php-fpm gerado por migrate-php-fpm em $(date '+%F %T')"
        echo "; VirtualHost: $f"
        echo "; ServerName: ${sn:--}  ServerAlias: ${sas:--}"
        echo "; DocumentRoot: ${dr:--}"
        echo "[$sec]"
        echo
        echo "user = $FPM_USER"
        echo "group = $FPM_GROUP"
        echo
        echo "listen = $sock"
        echo "listen.owner = www-data"
        echo "listen.group = www-data"
        echo "listen.mode = 0660"
        echo
        echo "pm = $PM"
        echo "pm.max_children = $PM_MAX_CHILDREN"
        echo "pm.start_servers = $PM_START_SERVERS"
        echo "pm.min_spare_servers = $PM_MIN_SPARE_SERVERS"
        echo "pm.max_spare_servers = $PM_MAX_SPARE_SERVERS"
        echo "pm.max_requests = $PM_MAX_REQUESTS"
        echo
        echo "request_terminate_timeout = $REQUEST_TERMINATE_TIMEOUT"
        local kk
        for kk in "${POOL_ORD[@]}"; do
            [[ -z ${POOL_LINHA[$kk]+x} ]] && continue
            [[ ${POOL_COM[$kk]} != padrão* ]] && echo "; ${POOL_COM[$kk]}"
            echo "${POOL_LINHA[$kk]}"
        done
    } >"$tp"

    echo
    info "Alterações no vhost ($f):"
    diff -u "$f" "$tv" | tail -n +3 | sed -e "s/^+/${G}+/" -e "s/^-/${R}-/" -e "s/\$/${N}/"
    log "DIFF $f:"$'\n'"$(diff -u "$f" "$tv")"
    echo
    info "Novo pool: $pool_file"
    sed 's/^/    /' "$tp"
    log "POOL $pool_file:"$'\n'"$(cat "$tp")"

    if (( ! AUTO )) && ! perguntar_sn "Aplicar estas alterações?" s; then
        rm -f "$tv" "$tp"; resumo_add "$fb" "$rotulo" "$nome" "ignorado" "não aplicado"; return 0
    fi
    if (( DRY_RUN )); then
        NOMES_USADOS[$nome]=1
        rm -f "$tv" "$tp"
        resumo_add "$fb" "$rotulo" "$nome" "simulado" "nada alterado"
        (( AUTO )) && { _continuar || return 2; }
        return 0
    fi

    # ---- aplicar
    local bak="$f.bak-$TS" feito=()
    cp -p "$f" "$bak" && ok "Backup: $bak" && feito+=("Backup|$bak")
    log "BACKUP $f -> $bak"
    cat "$tv" >"$f" && ok "Vhost alterado: $f" && feito+=("Vhost|${#pidx[@]} diretivas comentadas, SetHandler em ${portas% }")
    mkdir -p "$POOL_DIR"
    cp "$tp" "$pool_file" && chmod 644 "$pool_file" && ok "Pool criado: $pool_file" && feito+=("Pool|$pool_file")
    feito+=("Socket|$sock")
    if [[ ! -e $errlog ]]; then touch "$errlog"; fi
    chown "$FPM_USER:$FPM_GROUP" "$errlog" 2>/dev/null; chmod 640 "$errlog"
    feito+=("error_log|$errlog")
    rm -f "$tv" "$tp"
    NOMES_USADOS[$nome]=1
    local ht_feitos=()
    if (( ht_conv )); then
        for ht in "${HT_LISTA[@]}"; do converter_htaccess "$ht" && ht_feitos+=("$ht"); done
        feito+=(".htaccess|${#ht_feitos[@]} convertido(s) para .user.ini")
    fi
    log "APLICADO $f pool=$pool_file sock=$sock"

    # ---- validar
    echo
    local valido=1
    validar_apache || valido=0
    validar_fpm || valido=0
    if (( ! valido )); then
        if perguntar_sn "Há erros. Reverter as alterações deste virtualhost?" s; then
            cp -p "$bak" "$f"; rm -f "$pool_file"
            for ht in "${ht_feitos[@]}"; do
                cp -p "$ht.bak-$TS" "$ht"
                local ui; ui="$(dirname "$ht")/.user.ini"
                if [[ -e $ui.bak-$TS ]]; then cp -p "$ui.bak-$TS" "$ui"; else rm -f "$ui"; fi
            done
            warn "Revertido: $f reposto do backup e $pool_file removido"
            resumo_add "$fb" "$rotulo" "$nome" "ERRO (revertido)" "ver log"
        else
            resumo_add "$fb" "$rotulo" "$nome" "ERRO" "corrigir à mão"
        fi
        (( AUTO )) && { _continuar || return 2; }
        return 0
    fi

    if (( AUTO )) || perguntar_sn "Fazer reload do php-fpm ($FPM_SERVICE) agora?" s; then
        if recarregar "$FPM_SERVICE"; then
            sleep 1
            if [[ -S $sock ]]; then ok "Socket ativo: $sock"; feito+=("php-fpm|reload OK, socket ativo")
            else warn "O socket $sock ainda não existe"; feito+=("php-fpm|reload feito, socket não encontrado"); fi
        fi
    fi
    resumo_add "$fb" "$rotulo" "$nome" "migrado" "$sock"

    if (( AUTO )); then
        echo
        info "Feito em $rotulo:"
        tbl_init "Ação|Detalhe"; for i in "${feito[@]}"; do tbl_add "$i"; done; tbl_show
        _continuar || return 2
    fi
    return 0
}

_continuar() {
    local op
    read -r -p "Enter = próximo virtualhost | a = abortar: " op || op=""
    [[ ${op,,} == a ]] && return 1
    return 0
}

migrar() {
    AUTO=$1
    titulo "Migração mod_php → php-fpm ($( ((AUTO)) && echo AUTO || echo um a um ))$( ((DRY_RUN)) && echo ' — SIMULAÇÃO')"
    verificar_requisitos || return 1
    carregar_sites
    if (( ${#EN_LIST[@]} == 0 )); then warn "Não há nada no sites-enabled"; return 0; fi

    local i alvo en key primeiro rc=0 avr
    avr=$(readlink -f "$SITES_AVAILABLE")
    for i in "${!EN_LIST[@]}"; do
        en=${EN_LIST[i]}; alvo=${EN_ALVO[i]}
        if [[ ! -f $alvo ]]; then
            warn "sites-enabled/$en: link quebrado — ignorado"; resumo_add "$en" "-" "-" "ignorado" "link quebrado"; continue
        fi
        if [[ $(dirname "$alvo") != "$avr" ]]; then
            warn "sites-enabled/$en não aponta para o sites-available (→ $alvo)."
            perguntar_sn "Alterar diretamente $alvo?" n || { resumo_add "$en" "-" "-" "ignorado" "fora do sites-available"; continue; }
        fi
        analisar_vhost "$alvo"; agrupar
        if (( ${#GRUPOS[@]} == 0 )); then
            info "$en: sem <VirtualHost> — ignorado"; resumo_add "$en" "-" "-" "ignorado" "sem VirtualHost"; continue
        fi
        local grupos=("${GRUPOS[@]}")
        primeiro=1
        for key in "${grupos[@]}"; do
            migrar_grupo "$alvo" "$en" "$key" "$primeiro"; rc=$?
            primeiro=0
            (( rc == 2 )) && break 2
        done
    done
    (( rc == 2 )) && warn "Migração abortada pelo utilizador"

    mostrar_resumo
    validar_tudo
    if (( ! DRY_RUN )) && [[ " ${RES_ESTADO[*]} " == *" migrado "* ]]; then
        if perguntar_sn "Fazer reload do Apache e do php-fpm agora?" s; then
            recarregar "$FPM_SERVICE"
            if validar_apache >/dev/null; then recarregar apache2; else err "Apache com erros — reload não feito"; fi
        fi
        if mod_php_ativo && perguntar_sn "Desativar já o mod_php ($MOD_PHP)?" n; then
            desativar_mod_php
        fi
    fi
}

RES_IMPRESSO=0
mostrar_resumo() {
    RES_IMPRESSO=${#RES_FICH[@]}
    titulo "Resumo"
    if (( ${#RES_FICH[@]} == 0 )); then info "Nenhuma operação"; return; fi
    tbl_init "Ficheiro|Site|Pool|Estado|Nota"
    local i
    for i in "${!RES_FICH[@]}"; do tbl_add "${RES_FICH[i]}|${RES_SITE[i]}|${RES_POOL[i]}|${RES_ESTADO[i]}|${RES_NOTA[i]}"; done
    tbl_show
    info "Log: $LOG_FILE"
}

# ---------------------------------------------------------- 7) desativar
desativar_mod_php() {
    titulo "Desativar o mod_php ($MOD_PHP)"
    if ! mod_php_ativo; then ok "O mod_php já não está ativo"; else
    carregar_sites
    local pend=() semfpm=() i f l alvo
    # diretivas php_* ainda ativas (dão erro de sintaxe sem o mod_php)
    for alvo in "${EN_ALVO[@]}" "$APACHE_DIR"/conf-enabled/*.conf "$APACHE_DIR/apache2.conf"; do
        [[ -f $alvo ]] || continue
        while IFS=: read -r l _; do pend+=("$alvo:$l"); done < <(grep -nEi '^[[:space:]]*php_(admin_)?(value|flag)[[:space:]]' "$alvo")
    done
    # vhosts ativos com DocumentRoot sem handler do php-fpm (o código PHP seria servido como texto!)
    for i in "${!EN_ALVO[@]}"; do
        alvo=${EN_ALVO[i]}; [[ -f $alvo ]] || continue
        analisar_vhost "$alvo"
        [[ $(estado_vhost) == mod_php* ]] && semfpm+=("${EN_LIST[i]}")
    done
    if (( ${#pend[@]} )); then
        err "Ainda há diretivas php_* ativas (o Apache não arranca sem o mod_php):"
        for i in "${pend[@]}"; do mostrar_linha "${i%:*}" "${i##*:}"; done
        warn "Migre esses vhosts (opção 3/4) ou corrija à mão antes de desativar o mod_php."
        return 1
    fi
    if (( ${#semfpm[@]} )); then
        warn "VirtualHosts ativos ainda sem php-fpm: ${semfpm[*]}"
        warn "Sem mod_php, os .php destes sites seriam servidos como texto (código-fonte visível)!"
        if [[ -e $APACHE_DIR/conf-available/php$PHP_VER-fpm.conf ]] && perguntar_sn "Ativar a conf php$PHP_VER-fpm (envia-os para o pool www por defeito)?" s; then
            run "$A2ENCONF" "php$PHP_VER-fpm"
        elif ! perguntar_sn "Continuar mesmo assim?" n; then
            return 1
        fi
    fi
    local htp=() dr
    for alvo in "${EN_ALVO[@]}"; do
        [[ -f $alvo ]] || continue; analisar_vhost "$alvo"
        for i in "${!B_DR[@]}"; do dr=${B_DR[i]}; procurar_htaccess "$dr"; htp+=("${HT_LISTA[@]}"); done
    done
    if (( ${#htp[@]} )); then
        warn ".htaccess ainda com php_* (erro 500 sem o mod_php):"
        printf '    %s\n' "${htp[@]}" | sort -u
        perguntar_sn "Continuar mesmo assim?" n || return 1
    fi

    run "$A2DISMOD" "$MOD_PHP" || { err "Falhou o a2dismod $MOD_PHP"; return 1; }
    local mpm=0
    if modulo_ativo mpm_prefork && perguntar_sn "Trocar mpm_prefork por mpm_event (recomendado com php-fpm)?" s; then
        run "$A2DISMOD" mpm_prefork && run "$A2ENMOD" mpm_event && mpm=1
    fi
    modulo_ativo proxy_fcgi || run "$A2ENMOD" proxy_fcgi setenvif
    if (( ! DRY_RUN )) && ! validar_apache; then
        err "Configuração inválida — a repor o mod_php"
        (( mpm )) && { run "$A2DISMOD" mpm_event; run "$A2ENMOD" mpm_prefork; }
        run "$A2ENMOD" "$MOD_PHP"
        return 1
    fi
    if perguntar_sn "Fazer restart do Apache agora (necessário para a troca de módulos)?" s; then
        if (( DRY_RUN )); then info "[simulação] restart apache2"
        elif run servico restart apache2; then ok "Apache reiniciado"; else err "Falhou o restart do Apache"; fi
    fi
    resumo_add "-" "-" "-" "mod_php desativado" "$MOD_PHP$( ((mpm)) && echo ', mpm_event')"
    fi

    titulo "O que falta para desinstalar o mod_php"
    tbl_init "Passo|Comando"
    tbl_add "1. Testar os sites (php-fpm a responder)|curl -I https://<site>/  e  tail -f $PHP_LOG_DIR/<nome>.php.error.log"
    tbl_add "2. Ver o que depende do pacote|apt-cache rdepends --installed libapache2-mod-php$PHP_VER"
    tbl_add "3. Remover o mod_php|sudo apt purge libapache2-mod-php$PHP_VER libapache2-mod-php"
    tbl_add "4. Limpar dependências|sudo apt autoremove --purge"
    tbl_add "5. (opcional) php.ini do apache2|$PHP_ETC/$PHP_VER/apache2/ deixa de ser usado"
    tbl_add "6. Apagar backups antigos|$SITES_AVAILABLE/*.bak-*  e  *.delete"
    tbl_show
}

# ---------------------------------------------------------- menu
cabecalho() {
    titulo "migrate-php-fpm $VERSION$( ((DRY_RUN)) && echo '  [SIMULAÇÃO]')"
    tbl_init "Item|Valor"
    tbl_add "sites-available|$SITES_AVAILABLE"
    tbl_add "sites-enabled|$SITES_ENABLED"
    tbl_add "Versão PHP|$PHP_VER"
    tbl_add "mod_php ($MOD_PHP)|$(mod_php_ativo && echo ativo || echo inativo)"
    tbl_add "php-fpm|$(fpm_instalado && echo "instalado ($FPM_SERVICE)" || echo 'NÃO instalado')"
    tbl_add "pool.d|$POOL_DIR"
    tbl_add "Conversão|$( ((CONVERTER_PARA_ADMIN)) && echo 'php_value → php_admin_value' || echo 'mantém o tipo')"
    tbl_add "Log|$LOG_FILE"
    tbl_show
}

menu() {
    local op
    while true; do
        cabecalho
        cat <<EOF

  1) Validar virtualhosts (sites-available vs sites-enabled)
  2) Mostrar não usados e marcar como .delete
  3) Migrar mod_php → php-fpm (um a um, com confirmação)
  4) Migrar mod_php → php-fpm (AUTO, Enter entre cada virtualhost)
  5) Comparar php.ini e extensões (apache2 vs fpm)
  6) Validar configurações (Apache + php-fpm)
  7) Desativar o mod_php e indicar o que falta para desinstalar
  0) Sair
EOF
        read -r -p "Opção: " op || op=0
        log "MENU opção $op"
        case $op in
            1) validar_vhosts ;;
            2) marcar_delete ;;
            3) migrar 0 ;;
            4) migrar 1 ;;
            5) comparar_ini ;;
            6) validar_tudo ;;
            7) desativar_mod_php ;;
            0) break ;;
            *) warn "Opção inválida" ;;
        esac
        echo; read -r -p "Enter para voltar ao menu..." _ || break
    done
}

fim() {
    if (( ${#RES_FICH[@]} > RES_IMPRESSO )); then mostrar_resumo; else info "Log: $LOG_FILE"; fi
    log "===== fim ====="
}
trap 'echo; warn "Interrompido"; fim; exit 130' INT

detetar_php
case $MODO in
    "")        menu ;;
    validar)   validar_vhosts ;;
    delete)    marcar_delete ;;
    migrar)    migrar 0 ;;
    auto)      migrar 1 ;;
    ini)       comparar_ini ;;
    testar)    validar_tudo ;;
    desativar) desativar_mod_php ;;
    *) err "Modo desconhecido: $MODO"; uso; exit 1 ;;
esac
fim
