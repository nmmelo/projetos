#!/usr/bin/env bash
#
# setup.sh - configuração do mount-islive
#
# 1.ª execução : pede todas as configurações (mounts e pasta do ficheiro de teste, intervalo, SMTP, emails, agendamento),
#                grava config/.env e instala o agendamento (systemd, cron ou manual).
# Execuções seguintes: mostra os valores atuais; Enter mantém o valor entre [ ].
#
# Uso:
#   sudo ./setup.sh               configurar / reconfigurar
#   sudo ./setup.sh --show        mostrar a configuração atual e a linha do crontab
#   sudo ./setup.sh --cron        mostrar só a linha a colocar no crontab
#   sudo ./setup.sh --systemd     mostrar a configuração e os passos do timer systemd
#   sudo ./setup.sh --test-mail   enviar um email de teste
#   sudo ./setup.sh --uninstall   remover o agendamento (mantém config e logs)

set -euo pipefail

APP="mount-islive"
APP_HOME="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
CONF_DIR="${APP_HOME}/config"
ENV_FILE="${CONF_DIR}/.env"
KEY_FILE="${CONF_DIR}/smtp.key"
CHECK="${APP_HOME}/bin/check_mount.sh"
UNIT_DIR="/etc/systemd/system"
CRON_FILE="/etc/cron.d/${APP}"

VARS=(MOUNTS TEST_DIRS INTERVAL_MIN TEST_FILE TOUCH_TIMEOUT NOTIFY_ON_FAIL
      SMTP_SERVER SMTP_PORT SMTP_SECURITY SMTP_TLS_VERIFY SMTP_AUTH SMTP_USER SMTP_PASSWORD_ENC
      MAIL_FROM MAIL_TO SCHEDULER)

die()  { echo "Erro: $*" >&2; exit 1; }
line() { printf '%*s\n' 70 '' | tr ' ' '-'; }

[[ $EUID -eq 0 ]] || die "execute como root: sudo $0 $*"
for c in python3 openssl findmnt mountpoint timeout flock; do
    command -v "$c" >/dev/null || die "falta o comando '$c'"
done

have_systemd() { [[ -d /run/systemd/system ]]; }

load_env() {
    FIRST_RUN=1
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        FIRST_RUN=0
    fi
    # compatibilidade com a versão anterior (SMTP_SECURITY=none)
    [[ "${SMTP_SECURITY:-}" == "none" ]] && SMTP_SECURITY="nao"
    if [[ -z "${SMTP_AUTH:-}" && $FIRST_RUN -eq 0 ]]; then
        [[ -n "${SMTP_USER:-}" ]] && SMTP_AUTH="sim" || SMTP_AUTH="nao"
    fi
    return 0
}

# Expressão cron para o intervalo (minutos)
cron_expr() {
    local n="$1"
    if (( n < 60 )); then
        echo "*/${n} * * * *"
    elif (( n % 60 == 0 && n / 60 < 24 )); then
        echo "0 */$(( n / 60 )) * * *"
    elif (( n == 1440 )); then
        echo "0 0 * * *"
    else
        return 1
    fi
}

cron_lines() {
    local expr
    expr="$(cron_expr "${INTERVAL_MIN}")" || { echo "  (intervalo de ${INTERVAL_MIN} min não é representável em cron: use 1-59, múltiplos de 60 até 1380, ou 1440)"; return 0; }
    echo "  crontab do root (sudo crontab -e):"
    echo "    ${expr} ${CHECK} >/dev/null 2>&1"
    echo "  ou ficheiro ${CRON_FILE} (com utilizador):"
    echo "    ${expr} root ${CHECK} >/dev/null 2>&1"
}

show_config() {
    local v val
    line
    printf ' Configuração atual (%s)\n' "$ENV_FILE"
    line
    for v in "${VARS[@]}"; do
        val="${!v:-}"
        case "$v" in
            SMTP_PASSWORD_ENC) [[ -n "$val" ]] && val="(guardada - não é mostrada)" || val="(sem password)" ;;
            MOUNTS) local -a _m; read -ra _m <<< "$val"; val="${#_m[@]} mount(s)" ;;
            TEST_DIRS) continue ;;
        esac
        printf ' %-18s %s\n' "$v" "${val:--}"
        if [[ "$v" == "MOUNTS" ]]; then
            local -a _d; local i
            read -ra _d <<< "${TEST_DIRS:-}"
            for i in "${!_m[@]}"; do
                printf '   %-30s teste em %s/%s\n' "${_m[$i]}" "${_d[$i]:-${_m[$i]}}" "${TEST_FILE:-teste.txt}"
            done
        fi
    done
    line
    if systemctl is-enabled "${APP}.timer" >/dev/null 2>&1; then
        echo " Agendamento : timer systemd ativo"
    elif [[ -f "$CRON_FILE" ]]; then
        echo " Agendamento : cron (${CRON_FILE})"
    elif crontab -l 2>/dev/null | grep -qF "$CHECK"; then
        echo " Agendamento : crontab do root"
    else
        echo " Agendamento : não instalado (manual)"
    fi
    cron_lines
    line
}

# ask VAR "Pergunta" "valor por omissão"
ask() {
    local __var="$1" label="$2" def="${3:-}" ans=""
    if [[ -n "$def" ]]; then
        read -r -p "  ${label} [${def}]: " ans || true
    else
        read -r -p "  ${label}: " ans || true
    fi
    printf -v "$__var" '%s' "${ans:-$def}"
}

# ask_req VAR "Pergunta" "omissão" [regex] [mensagem de erro]
ask_req() {
    local __var="$1" label="$2" def="${3:-}" re="${4:-.+}" err="${5:-valor inválido}" n=0
    while true; do
        ask "$__var" "$label" "$def"
        [[ "${!__var}" =~ $re ]] && return 0
        echo "    -> ${err}"
        (( ++n < 5 )) || die "demasiadas respostas inválidas em '${label}'"
    done
}

ask_yn() {   # ask_yn VAR "Pergunta" "sim|nao"
    ask_req "$1" "$2 (sim/nao)" "$3" '^(sim|nao)$' "responda sim ou nao"
}

# Pasta onde é escrito o ficheiro de teste: tem de estar dentro do mount
ask_test_dir() {   # ask_test_dir VAR mount omissão
    local __var="$1" mp="$2" def="$3" d cont rc
    while true; do
        ask_req d "  Pasta onde gravar o ${TEST_FILE:-teste.txt} (dentro de ${mp})" "$def" '^/[^ ]*$' "caminho absoluto, sem espaços"
        d="${d%/}"; [[ -z "$d" ]] && d="/"
        if [[ "$d" != "$mp" && "$d" != "${mp%/}/"* ]]; then
            echo "    -> a pasta tem de ser ${mp} ou uma subpasta (ex.: ${mp%/}/monitor)"; continue
        fi
        if ! mountpoint -q "$mp" 2>/dev/null; then
            echo "    !!  ${mp} não está montado agora: não foi possível validar a pasta"
            break
        fi
        if ! timeout -k 2 10 test -d "$d"; then
            echo "    !!  ${d} não existe"
            ask_yn cont "  Criar a pasta agora?" "sim"
            if [[ "$cont" == "sim" ]]; then
                timeout -k 2 10 mkdir -p "$d" || { echo "    -> não foi possível criar ${d}"; continue; }
            else
                continue
            fi
        fi
        timeout -k 2 10 bash -c 'f="$1/.mount-islive.$$"; echo ok > "$f" && rm -f "$f"' _ "$d" 2>/dev/null; rc=$?
        if (( rc == 0 )); then
            echo "    ok  escrita em ${d} validada"
            break
        fi
        echo "    !!  sem permissão de escrita em ${d} (ou sem resposta)"
        ask_yn cont "  Usar mesmo assim?" "nao"
        [[ "$cont" == "sim" ]] && break
    done
    printf -v "$__var" '%s' "$d"
}

# Limpa o estado de falha de um mount (usado quando a pasta de teste muda)
clear_state() {   # $1 = mount
    local k="${1//\//_}"; k="${k:-_root}"
    rm -f "${APP_HOME}/state/${k}".{down,tries,reason,alerted,lastok,file}
}

ask_mounts() {
    local -a old new olddirs newdirs
    local count i mp cont td
    read -ra old <<< "${MOUNTS:-}"
    read -ra olddirs <<< "${TEST_DIRS:-}"
    ask_req count "Quantos mounts quer validar" "${#old[@]}" '^[1-9][0-9]?$' "número entre 1 e 99"
    [[ "$count" == "0" ]] && count=1
    new=(); newdirs=()
    for (( i = 0; i < count; i++ )); do
        while true; do
            ask_req mp "Mount $(( i + 1 )) de ${count} (caminho, ex.: /mnt/nfs)" "${old[$i]:-}" '^/[^ ]*$' "caminho absoluto, sem espaços"
            if [[ " ${new[*]} " == *" ${mp} "* ]]; then
                echo "    -> ${mp} já foi indicado"; continue
            fi
            if findmnt -s -n "$mp" >/dev/null 2>&1; then
                echo "    ok  ${mp} -> $(findmnt -s -n -o SOURCE,FSTYPE "$mp" | xargs)"
                break
            fi
            echo "    !!  ${mp} não está no /etc/fstab (o remount automático usa o fstab)"
            ask_yn cont "Usar mesmo assim?" "nao"
            [[ "$cont" == "sim" ]] && break
        done
        # se o mount mudou, a pasta anterior deixa de servir como omissão
        td="${olddirs[$i]:-}"
        [[ "${old[$i]:-}" == "$mp" && -n "$td" ]] || td="$mp"
        ask_test_dir td "$mp" "$td"
        new+=("$mp"); newdirs+=("$td")
        # falha anterior registada com outra pasta de teste: deixa de ser válida
        if [[ -f "${APP_HOME}/state/$(k="${mp//\//_}"; echo "${k:-_root}").down" ]]; then
            local j prev=""
            for j in "${!old[@]}"; do [[ "${old[$j]}" == "$mp" ]] && prev="${olddirs[$j]:-$mp}"; done
            if [[ "$prev" != "$td" ]]; then
                clear_state "$mp"
                echo "    estado de falha anterior de ${mp} limpo (a pasta de teste mudou)"
            fi
        fi
    done
    MOUNTS="${new[*]}"
    TEST_DIRS="${newdirs[*]}"
}

ask_password() {
    local pw pw2
    if [[ -n "${SMTP_PASSWORD_ENC:-}" ]]; then
        echo "  Password: já está guardada (não é possível vê-la)."
        echo "  Enter = manter | escrever uma nova = substituir"
    fi
    while true; do
        read -r -s -p "  Password: " pw || true; echo
        if [[ -z "$pw" ]]; then
            [[ -n "${SMTP_PASSWORD_ENC:-}" ]] && return 0
            echo "    -> a password é obrigatória com autenticação"; continue
        fi
        read -r -s -p "  Repetir password: " pw2 || true; echo
        [[ "$pw" == "$pw2" ]] && break
        echo "    -> as passwords não coincidem"
    done
    if [[ ! -s "$KEY_FILE" ]]; then
        (umask 077; openssl rand -base64 48 > "$KEY_FILE")
    fi
    chmod 600 "$KEY_FILE"
    SMTP_PASSWORD_ENC="$(printf '%s' "$pw" | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -a -A -pass "file:${KEY_FILE}")"
    unset pw pw2
    echo "    password guardada cifrada (não fica em texto no .env)"
}

save_env() {
    local v val tmp
    mkdir -p "$CONF_DIR"
    chmod 700 "$CONF_DIR"
    tmp="$(mktemp "${CONF_DIR}/.env.XXXXXX")"
    {
        echo "# ${APP} - configuração gerada por setup.sh em $(date '+%Y-%m-%d %H:%M:%S')"
        echo "# Para alterar: sudo ${APP_HOME}/setup.sh"
        for v in "${VARS[@]}"; do
            val="${!v:-}"
            printf "%s='%s'\n" "$v" "${val//\'/\'\\\'\'}"
        done
    } > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
    echo "  Configuração gravada em ${ENV_FILE} (chmod 600)"
}

remove_schedule() {
    if [[ -f "${UNIT_DIR}/${APP}.timer" ]]; then
        systemctl disable --now "${APP}.timer" >/dev/null 2>&1 || true
        rm -f "${UNIT_DIR}/${APP}.timer" "${UNIT_DIR}/${APP}.service"
        systemctl daemon-reload 2>/dev/null || true
    fi
    rm -f "$CRON_FILE"
    if command -v crontab >/dev/null && crontab -l 2>/dev/null | grep -qF "$CHECK"; then
        crontab -l 2>/dev/null | grep -vF "$CHECK" | crontab -
    fi
}

unit_service() {
    cat <<EOF
[Unit]
Description=${APP} - valida se os mounts estão vivos e refaz o mount
After=network-online.target remote-fs.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${CHECK}
EOF
}

unit_timer() {
    cat <<EOF
[Unit]
Description=${APP} - a cada ${INTERVAL_MIN} minuto(s)

[Timer]
OnActiveSec=1min
OnBootSec=2min
OnUnitActiveSec=${INTERVAL_MIN}min
AccuracySec=15s
Unit=${APP}.service

[Install]
WantedBy=timers.target
EOF
}

# --systemd: estado atual e passos para usar o timer systemd
systemd_steps() {
    line
    echo " Agendamento por systemd"
    line
    if ! have_systemd; then
        echo "  Este sistema NÃO tem systemd (ex.: container). Use cron: sudo ${APP_HOME}/setup.sh --cron"
        line; return 0
    fi
    if systemctl is-enabled "${APP}.timer" >/dev/null 2>&1; then
        echo "  Estado: timer ${APP}.timer INSTALADO e $(systemctl is-active "${APP}.timer" 2>/dev/null)"
        echo "  Nada a fazer. Para alterar o intervalo, corra o setup e indique o novo valor."
    else
        echo "  Estado: timer ${APP}.timer NÃO instalado (agendamento atual: ${SCHEDULER:-?})"
    fi
    echo
    echo "  Opção A - pelo setup (recomendado):"
    echo "    1. sudo ${APP_HOME}/setup.sh"
    echo "    2. Enter nos valores a manter; em [4/4] Agendamento escrever: systemd"
    echo "       (o setup remove a linha do cron, se existir, e instala e ativa o timer)"
    echo
    echo "  Opção B - à mão:"
    echo "    1. sudo ${APP_HOME}/setup.sh --uninstall        # remove a linha do cron, se existir"
    echo "    2. criar ${UNIT_DIR}/${APP}.service:"
    unit_service | sed 's/^/         /'
    echo "    3. criar ${UNIT_DIR}/${APP}.timer:"
    unit_timer   | sed 's/^/         /'
    echo "    4. sudo systemctl daemon-reload"
    echo "       sudo systemctl enable --now ${APP}.timer"
    echo "    (com a opção B o .env continua com SCHEDULER=${SCHEDULER:-?}; o setup é que o atualiza)"
    echo
    echo "  Confirmar:"
    echo "    systemctl list-timers ${APP}.timer          # próxima execução"
    echo "    systemctl status ${APP}.timer"
    echo "    journalctl -u ${APP}.service -n 50          # últimas execuções"
    echo "    sudo systemctl start ${APP}.service         # correr já uma vez"
    echo "    tail -f ${APP_HOME}/logs/mount-islive.log"
    echo "  Verificar que não ficou também no cron: sudo crontab -l ; ls ${CRON_FILE}"
    line
}

install_schedule() {
    local expr
    chmod +x "$CHECK" "${APP_HOME}/bin/send_mail.py"
    remove_schedule
    case "$SCHEDULER" in
        systemd)
            unit_service > "${UNIT_DIR}/${APP}.service"
            unit_timer   > "${UNIT_DIR}/${APP}.timer"
            systemctl daemon-reload
            systemctl enable --now "${APP}.timer" >/dev/null
            systemctl restart "${APP}.timer"
            echo "  Timer systemd instalado: ${APP}.timer (a cada ${INTERVAL_MIN} min)"
            ;;
        cron)
            expr="$(cron_expr "$INTERVAL_MIN")" || die "intervalo ${INTERVAL_MIN} não suportado em cron"
            if command -v crontab >/dev/null; then
                ( crontab -l 2>/dev/null | grep -vF "$CHECK"; echo "${expr} ${CHECK} >/dev/null 2>&1" ) | crontab -
                echo "  Linha adicionada ao crontab do root"
            else
                echo "${expr} root ${CHECK} >/dev/null 2>&1" > "$CRON_FILE"
                chmod 644 "$CRON_FILE"
                echo "  Criado ${CRON_FILE}"
            fi
            ;;
        manual)
            echo "  Nenhum agendamento instalado (ver os passos no fim)"
            ;;
    esac
}

# Mostra o que cada opção de agendamento faz (antes da escolha)
explain_schedulers() {
    local expr
    expr="$(cron_expr "$INTERVAL_MIN" 2>/dev/null)" || expr="(intervalo não representável em cron)"
    echo "  Opções:"
    if have_systemd; then
        echo "    systemd : o setup cria e ativa ${APP}.service + ${APP}.timer (a cada ${INTERVAL_MIN} min);"
        echo "              não há nada a fazer à mão (passos: sudo ${APP_HOME}/setup.sh --systemd)"
    else
        echo "    systemd : (não disponível)"
    fi
    if command -v crontab >/dev/null; then
        echo "    cron    : o setup acrescenta ao crontab do root a linha:"
        echo "                ${expr} ${CHECK} >/dev/null 2>&1"
    else
        echo "    cron    : o setup cria ${CRON_FILE} com a linha:"
        echo "                ${expr} root ${CHECK} >/dev/null 2>&1"
    fi
    echo "    manual  : não instala nada; mostra a linha para colocar no crontab"
}

# Confirma o que vai ser feito com a opção escolhida
explain_choice() {
    local expr
    expr="$(cron_expr "$INTERVAL_MIN" 2>/dev/null || true)"
    case "$SCHEDULER" in
        systemd)
            echo "  -> vão ser criados ${UNIT_DIR}/${APP}.service e ${APP}.timer e ativado o timer"
            echo "     (systemctl daemon-reload + systemctl enable --now ${APP}.timer)"
            ;;
        cron)
            if command -v crontab >/dev/null; then
                echo "  -> vai ser acrescentada ao crontab do root a linha:"
            else
                echo "  -> comando crontab não existe; vai ser criado ${CRON_FILE} com:"
                expr="${expr} root"
            fi
            echo "       ${expr} ${CHECK} >/dev/null 2>&1"
            ;;
        manual)  echo "  -> nada vai ser instalado; no fim são mostrados os passos a fazer" ;;
    esac
}

# Passos finais / como confirmar, conforme o agendamento
next_steps() {
    local expr
    expr="$(cron_expr "$INTERVAL_MIN" 2>/dev/null || true)"
    echo
    line
    echo " Agendamento (${SCHEDULER}): o que fazer / como confirmar"
    line
    case "$SCHEDULER" in
        systemd)
            echo "  Nada a fazer: criados ${UNIT_DIR}/${APP}.service e ${APP}.timer,"
            echo "  timer ativo (a cada ${INTERVAL_MIN} min, também após reboot)."
            echo "  Confirmar : systemctl list-timers ${APP}.timer"
            echo "  Execuções : journalctl -u ${APP}.service -n 50"
            echo "  Correr já : sudo systemctl start ${APP}.service"
            echo "  Detalhe e passos à mão: sudo ${APP_HOME}/setup.sh --systemd"
            ;;
        cron)
            if [[ -f "$CRON_FILE" ]]; then
                echo "  Linha instalada em ${CRON_FILE}. Confirmar: cat ${CRON_FILE}"
                echo "    ${expr} root ${CHECK} >/dev/null 2>&1"
            else
                echo "  Linha instalada no crontab do root. Confirmar: sudo crontab -l"
                echo "    ${expr} ${CHECK} >/dev/null 2>&1"
            fi
            if pgrep -x cron >/dev/null || pgrep -x crond >/dev/null; then
                echo "  Serviço cron: a correr. Nada mais a fazer."
            else
                echo "  Serviço cron: NÃO está a correr. Falta iniciá-lo:"
                echo "    Debian/Ubuntu : service cron start      (container: 'cron' no entrypoint)"
                echo "    RHEL/Oracle L.: service crond start     (container: 'crond' no entrypoint)"
            fi
            ;;
        manual)
            echo "  Falta agendar. Opção A - crontab do root:"
            echo "    1. sudo crontab -e"
            echo "    2. acrescentar a linha:"
            echo "       ${expr} ${CHECK} >/dev/null 2>&1"
            echo "  Opção B - ficheiro em /etc/cron.d:"
            echo "    echo '${expr} root ${CHECK} >/dev/null 2>&1' | sudo tee ${CRON_FILE}"
            echo "  Confirmar: sudo crontab -l   (ou cat ${CRON_FILE})"
            ;;
    esac
    echo "  Ver de novo a qualquer momento: sudo ${APP_HOME}/setup.sh --cron"
    line
}

test_mail() {
    local pw=""
    if [[ "${SMTP_AUTH:-nao}" == "sim" && -n "${SMTP_PASSWORD_ENC:-}" ]]; then
        pw="$(printf '%s' "$SMTP_PASSWORD_ENC" | openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -a -A -pass "file:${KEY_FILE}")" \
            || die "não foi possível ler a password guardada; corra o setup e indique-a de novo"
    fi
    (
        set -a; source "$ENV_FILE"; set +a
        [[ "${SMTP_AUTH:-nao}" == "sim" ]] || SMTP_USER=""
        SMTP_PASSWORD="$pw" python3 "${APP_HOME}/bin/send_mail.py" "[$(hostname)] ${APP}: email de teste" <<EOF
Email de teste do ${APP} enviado em $(date '+%d/%m/%Y %H:%M:%S').

Mounts monitorizados : ${MOUNTS}
Pastas de teste      : ${TEST_DIRS:-${MOUNTS}}
Intervalo            : ${INTERVAL_MIN} minuto(s)
EOF
    )
}

# --- opções ------------------------------------------------------------------
load_env
case "${1:-}" in
    --show)      [[ $FIRST_RUN -eq 0 ]] || die "ainda não configurado"; show_config; exit 0 ;;
    --cron)      [[ $FIRST_RUN -eq 0 ]] || die "ainda não configurado"; cron_lines; exit 0 ;;
    --systemd)   [[ $FIRST_RUN -eq 0 ]] || die "ainda não configurado"; show_config; systemd_steps; exit 0 ;;
    --test-mail) [[ $FIRST_RUN -eq 0 ]] || die "ainda não configurado"; test_mail; exit $? ;;
    --uninstall) remove_schedule; echo "Agendamento removido. A configuração e os logs foram mantidos."; exit 0 ;;
    "" ) ;;
    *) sed -n '3,16p' "$0"; exit 1 ;;
esac

# --- assistente --------------------------------------------------------------
echo
if (( FIRST_RUN )); then
    echo "=== ${APP}: primeira configuração ==="
else
    echo "=== ${APP}: reconfiguração ==="
    show_config
    echo "  Enter mantém o valor entre [ ]."
fi

echo; echo "[1/4] Mounts"
ask_req TEST_FILE "Nome do ficheiro de teste" "${TEST_FILE:-teste.txt}" '^[^/ ]+$' "só o nome, sem / nem espaços"
ask_mounts
while true; do
    ask_req INTERVAL_MIN "Intervalo entre testes, em minutos" "${INTERVAL_MIN:-5}" '^[1-9][0-9]{0,3}$' "número entre 1 e 1440"
    (( INTERVAL_MIN <= 1440 )) && break
    echo "    -> número entre 1 e 1440"
done
ask_req TOUCH_TIMEOUT "Timeout do teste de escrita, em segundos" "${TOUCH_TIMEOUT:-15}" '^[1-9][0-9]*$' "número inteiro"
ask_yn NOTIFY_ON_FAIL "Enviar também alerta se o remount falhar" "${NOTIFY_ON_FAIL:-sim}"

echo; echo "[2/4] Servidor SMTP"
ask_req SMTP_SERVER "Servidor SMTP (DNS ou IP)" "${SMTP_SERVER:-}" '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$' "nome DNS ou endereço IP"
ask_req SMTP_SECURITY "TLS (nao / starttls / ssl)" "${SMTP_SECURITY:-nao}" '^(nao|starttls|ssl)$' "nao, starttls ou ssl"
case "$SMTP_SECURITY" in ssl) dport=465 ;; starttls) dport=587 ;; *) dport=25 ;; esac
[[ "${SMTP_PORT:-}" =~ ^(25|465|587)$ && "${SMTP_PORT}" != "$dport" ]] && SMTP_PORT=""
ask_req SMTP_PORT "Porta" "${SMTP_PORT:-$dport}" '^[0-9]{1,5}$' "porta numérica"
if [[ "$SMTP_SECURITY" != "nao" ]]; then
    ask_yn SMTP_TLS_VERIFY "Validar o certificado TLS" "${SMTP_TLS_VERIFY:-nao}"
else
    SMTP_TLS_VERIFY="nao"
fi
ask_yn SMTP_AUTH "O servidor exige autenticação" "${SMTP_AUTH:-nao}"
if [[ "$SMTP_AUTH" == "sim" ]]; then
    ask_req SMTP_USER "Username" "${SMTP_USER:-}" '.+' "indique o username"
    ask_password
else
    SMTP_USER=""; SMTP_PASSWORD_ENC=""
fi

echo; echo "[3/4] Emails"
EMAIL_RE='^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
ask_req MAIL_FROM "Remetente" "${MAIL_FROM:-${APP}@$(hostname -f 2>/dev/null || hostname)}" "$EMAIL_RE" "email inválido"
while true; do
    ask_req MAIL_TO "Destinatário(s), separados por vírgula" "${MAIL_TO:-}" '.+@.+' "indique pelo menos um email"
    MAIL_TO="$(echo "$MAIL_TO" | tr ';' ',' | tr -d ' ')"
    bad=""
    IFS=',' read -ra _list <<< "$MAIL_TO"
    for e in "${_list[@]}"; do [[ "$e" =~ $EMAIL_RE ]] || bad+=" $e"; done
    [[ -z "$bad" ]] && break
    echo "    -> email(s) inválido(s):${bad}"
done

echo; echo "[4/4] Agendamento"
if have_systemd; then def_sched="systemd"; else def_sched="cron"; echo "  (systemd não disponível neste sistema, ex.: container)"; fi
explain_schedulers
while true; do
    ask_req SCHEDULER "Tipo (systemd / cron / manual)" "${SCHEDULER:-$def_sched}" '^(systemd|cron|manual)$' "systemd, cron ou manual"
    if [[ "$SCHEDULER" == "systemd" ]] && ! have_systemd; then echo "    -> systemd não disponível; escolha cron ou manual"; continue; fi
    if [[ "$SCHEDULER" != "systemd" ]] && ! cron_expr "$INTERVAL_MIN" >/dev/null; then
        echo "    -> o intervalo de ${INTERVAL_MIN} min não é representável em cron (use 1-59, múltiplos de 60, ou 1440)"; continue
    fi
    break
done
explain_choice

echo
save_env
install_schedule
mkdir -p "${APP_HOME}/logs" "${APP_HOME}/state"

echo
ask_yn _t "Enviar um email de teste agora" "sim"
[[ "$_t" == "sim" ]] && { test_mail || echo "  (verifique as configurações SMTP)"; }
ask_yn _c "Executar já uma verificação dos mounts" "sim"
[[ "$_c" == "sim" ]] && { "$CHECK" -v || true; }

next_steps
echo
echo "Concluído. Log: ${APP_HOME}/logs/mount-islive.log"
echo "Rever/alterar: sudo $0  |  ver: sudo $0 --show  |  linha do crontab: sudo $0 --cron"
