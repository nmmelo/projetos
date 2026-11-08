#!/usr/bin/env bash
#
# check_mount.sh - valida se os mounts configurados estão realmente vivos (mount-islive)
#
# Para cada mount em MOUNTS (config/.env):
#   1. Escreve a data/hora atual em <mount>/<TEST_FILE> (com timeout, para não pendurar
#      quando o servidor de rede deixa de responder) e lê o valor de volta.
#   2. Se falhar: regista a hora da falha, faz umount -f -l e volta a montar a partir do fstab.
#   3. Quando o teste volta a ter sucesso: envia email com a hora da falha e a hora do teste com sucesso.
#   4. Se continuar em baixo e NOTIFY_ON_FAIL=sim: envia um único email de alerta (até recuperar).
#
# Executado pelo timer systemd (ou cron) criado pelo setup.sh. Precisa de root.
#
# Uso: check_mount.sh [-v]     (-v mostra também os testes OK no ecrã)

set -uo pipefail

APP_HOME="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${APP_HOME}/config/.env}"
KEY_FILE="${KEY_FILE:-${APP_HOME}/config/smtp.key}"
STATE_DIR="${APP_HOME}/state"
LOG_DIR="${APP_HOME}/logs"
LOG_FILE="${LOG_DIR}/mount-islive.log"
VERBOSE=0
[[ "${1:-}" == "-v" ]] && VERBOSE=1

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Configuração não encontrada (${ENV_FILE}). Execute primeiro: ${APP_HOME}/setup.sh" >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

TEST_FILE="${TEST_FILE:-teste.txt}"
TOUCH_TIMEOUT="${TOUCH_TIMEOUT:-15}"
MOUNT_TIMEOUT="${MOUNT_TIMEOUT:-60}"
NOTIFY_ON_FAIL="${NOTIFY_ON_FAIL:-sim}"

mkdir -p "$STATE_DIR" "$LOG_DIR"
chmod 700 "$STATE_DIR"

HOST="$(hostname -f 2>/dev/null || hostname)"

now()      { date '+%Y-%m-%d %H:%M:%S'; }
fmt_date() { date -d "$1" '+%d/%m/%Y %H:%M:%S' 2>/dev/null || echo "$1"; }
log()      { echo "$(now) $*" >> "$LOG_FILE"; echo "$*"; }
log_q()    { echo "$(now) $*" >> "$LOG_FILE"; (( VERBOSE )) && echo "$*"; return 0; }
state_key(){ local k="${1//\//_}"; echo "${k:-_root}"; }

duration() {
    local a b s
    a=$(date -d "$1" +%s 2>/dev/null) || { echo "-"; return; }
    b=$(date -d "$2" +%s 2>/dev/null) || { echo "-"; return; }
    s=$(( b - a )); (( s < 0 )) && s=0
    printf '%dh %02dm %02ds' $(( s / 3600 )) $(( s % 3600 / 60 )) $(( s % 60 ))
}

# Evitar execuções sobrepostas (ex.: um mount pendurado mais tempo que o intervalo)
exec 9>"${STATE_DIR}/.lock"
if ! flock -n 9; then
    log_q "Outra execução ainda está a decorrer; a sair."
    exit 0
fi

# Password SMTP: guardada cifrada no .env, decifrada só no momento do envio
SMTP_PASSWORD=""
if [[ -n "${SMTP_PASSWORD_ENC:-}" ]]; then
    if ! SMTP_PASSWORD="$(printf '%s' "$SMTP_PASSWORD_ENC" | openssl enc -d -aes-256-cbc -pbkdf2 -a -A -pass "file:${KEY_FILE}" 2>/dev/null)"; then
        log "AVISO: não foi possível decifrar a password SMTP (${KEY_FILE}). Corra o setup.sh de novo."
        SMTP_PASSWORD=""
    fi
fi
export SMTP_PASSWORD

send_mail() {   # $1 = assunto, stdin = corpo
    local out
    if out="$(python3 "${APP_HOME}/bin/send_mail.py" "$1" 2>&1)"; then
        log "Email enviado: $1"
    else
        log "ERRO ao enviar email '$1': $out"
        return 1
    fi
}

# Teste: escreve a data no ficheiro de teste e confirma que a leitura devolve o mesmo valor.
test_mount() {   # $1 = mount point, $2 = data a escrever
    timeout -k 5 "$TOUCH_TIMEOUT" bash -c '
        mp="$1"; ts="$2"; f="$1/$3"
        mountpoint -q "$mp" || exit 10
        printf "%s\n" "$ts" > "$f" || exit 11
        [[ "$(cat "$f")" == "$ts" ]] || exit 12
    ' _ "$1" "$2" "$TEST_FILE"
}

reason() {
    case "$1" in
        10)  echo "não está montado" ;;
        11)  echo "não foi possível escrever em ${TEST_FILE}" ;;
        12)  echo "o conteúdo lido de ${TEST_FILE} não corresponde ao escrito" ;;
        124|137) echo "sem resposta em ${TOUCH_TIMEOUT}s (timeout)" ;;
        *)   echo "erro $1" ;;
    esac
}

remount() {   # $1 = mount point
    local mp="$1"
    if mountpoint -q "$mp" 2>/dev/null || grep -qs " ${mp} " /proc/mounts; then
        umount -f -l "$mp" >> "$LOG_FILE" 2>&1 || true
        sleep 2
    fi
    timeout -k 5 "$MOUNT_TIMEOUT" mount "$mp" >> "$LOG_FILE" 2>&1
}

notify_recovered() {   # $1 mp, $2 falha, $3 recuperado, $4 último OK antes da falha, $5 tentativas, $6 motivo
    local last="-"
    [[ -n "${4:-}" ]] && last="$(fmt_date "$4")"
    send_mail "[${HOST}] Mount ${1} recuperado - ON" <<EOF
O mount ${1} no servidor ${HOST} esteve em baixo e já está novamente ON.

  Servidor                      : ${HOST}
  Mount                         : ${1}
  Último teste OK antes da falha: ${last}
  Falha detetada                : $(fmt_date "$2")
  Motivo                        : ${6}
  Teste com sucesso (ON)        : $(fmt_date "$3")
  Tempo em baixo (aprox.)       : $(duration "$2" "$3")
  Tentativas de remount         : ${5}

O ficheiro ${1}/${TEST_FILE} foi escrito com a data do teste com sucesso.

--
mount-islive (verificação a cada ${INTERVAL_MIN:-?} min)
EOF
}

notify_down() {   # $1 mp, $2 falha, $3 motivo
    send_mail "[${HOST}] ALERTA: mount ${1} em baixo" <<EOF
O mount ${1} no servidor ${HOST} está em baixo e o remount automático falhou.

  Servidor        : ${HOST}
  Mount           : ${1}
  Falha detetada  : $(fmt_date "$2")
  Motivo          : ${3}

O remount vai ser tentado de novo a cada ${INTERVAL_MIN:-?} minutos.
Quando o mount voltar a ficar ON será enviado um novo email.

--
mount-islive
EOF
}

if [[ -z "${MOUNTS:-}" ]]; then
    log "Nenhum mount configurado (MOUNTS vazio). Execute o setup.sh."
    exit 1
fi

rc_global=0
for mp in $MOUNTS; do
    k="$(state_key "$mp")"
    f_down="${STATE_DIR}/${k}.down"         # hora da primeira falha
    f_ok="${STATE_DIR}/${k}.ok"             # hora do último teste OK
    f_try="${STATE_DIR}/${k}.tries"         # tentativas de remount
    f_reason="${STATE_DIR}/${k}.reason"     # motivo da falha
    f_alert="${STATE_DIR}/${k}.alerted"     # alerta de falha já enviado

    last_ok="$(cat "$f_ok" 2>/dev/null || true)"
    ts="$(now)"

    test_mount "$mp" "$ts"; rc=$?
    if (( rc == 0 )); then
        echo "$ts" > "$f_ok"
        if [[ -f "$f_down" ]]; then
            # Voltou sozinho (ex.: a rede regressou) depois de uma falha anterior
            log "OK ${mp}: recuperado (teste com sucesso às ${ts})"
            notify_recovered "$mp" "$(cat "$f_down")" "$ts" "$(cat "${STATE_DIR}/${k}.lastok" 2>/dev/null)" \
                "$(cat "$f_try" 2>/dev/null || echo 0)" "$(cat "$f_reason" 2>/dev/null || echo '-')"
            rm -f "$f_down" "$f_try" "$f_reason" "$f_alert" "${STATE_DIR}/${k}.lastok"
        else
            log_q "OK ${mp}"
        fi
        continue
    fi

    why="$(reason "$rc")"

    # Primeira falha: guardar a hora, o motivo e o último OK antes da falha
    if [[ ! -f "$f_down" ]]; then
        echo "$ts" > "$f_down"
        echo "$why" > "$f_reason"
        echo "$last_ok" > "${STATE_DIR}/${k}.lastok"
    fi
    tries=$(( $(cat "$f_try" 2>/dev/null || echo 0) + 1 ))
    echo "$tries" > "$f_try"

    log "FALHA ${mp}: ${why}. A refazer o mount (tentativa ${tries})..."
    remount "$mp" || log "  mount ${mp} devolveu erro (ver ${LOG_FILE})"

    ts2="$(now)"
    test_mount "$mp" "$ts2"; rc2=$?
    if (( rc2 == 0 )); then
        echo "$ts2" > "$f_ok"
        log "OK ${mp}: recuperado após remount (teste com sucesso às ${ts2})"
        notify_recovered "$mp" "$(cat "$f_down")" "$ts2" "$(cat "${STATE_DIR}/${k}.lastok" 2>/dev/null)" \
            "$tries" "$(cat "$f_reason")"
        rm -f "$f_down" "$f_try" "$f_reason" "$f_alert" "${STATE_DIR}/${k}.lastok"
    else
        log "FALHA ${mp}: continua em baixo após o remount ($(reason "$rc2"))."
        rc_global=1
        if [[ "$NOTIFY_ON_FAIL" == "sim" && ! -f "$f_alert" ]]; then
            notify_down "$mp" "$(cat "$f_down")" "$why" && touch "$f_alert"
        fi
    fi
done

exit $rc_global
