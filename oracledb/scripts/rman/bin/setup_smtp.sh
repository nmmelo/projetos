#!/usr/bin/env bash
# Define interativamente ORACLE_SID, diretório dos logs RMAN e variáveis SMTP e grava config/rman_monitor.conf.
# A password é cifrada (AES-256-CBC) com a chave config/smtp.key.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)/config"
CONF_FILE="${CONFIG_DIR}/rman_monitor.conf"
KEY_FILE="${CONFIG_DIR}/smtp.key"

command -v openssl >/dev/null 2>&1 || { echo "openssl é necessário." >&2; exit 1; }
mkdir -p -- "$CONFIG_DIR"
chmod +x -- "${SCRIPT_DIR}"/*.sh

if [[ ! -s "$KEY_FILE" ]]; then
    openssl rand -base64 48 > "$KEY_FILE"
    echo "Chave criada em ${KEY_FILE}"
fi
chmod 600 -- "$KEY_FILE"

# Valores atuais como predefinição
if [[ -r "$CONF_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONF_FILE"
fi

ask() { # nome, descrição, predefinição
    local var="$1" label="$2" def="${3:-}" value
    read -r -p "${label}${def:+ [${def}]}: " value
    printf -v "$var" '%s' "${value:-$def}"
}

ask ORACLE_SID "ORACLE_SID" "${ORACLE_SID:-}"
ask RMAN_LOG_DIR "Diretório onde são guardados os logs do backup RMAN" "${RMAN_LOG_DIR:-${FALLBACK_ORACLE_LOG:-}}"
ask SMTP_SERVER "Servidor SMTP" "${SMTP_SERVER:-}"
ask SMTP_PORT "Porta SMTP" "${SMTP_PORT:-25}"
ask SMTP_USE_TLS "Usar STARTTLS (true/false)" "${SMTP_USE_TLS:-false}"
ask SMTP_TLS_VERIFY "Validar certificado TLS/SSL (true/false)" "${SMTP_TLS_VERIFY:-false}"
ask SMTP_USERNAME "Utilizador SMTP (vazio = sem autenticação)" "${SMTP_USERNAME:-}"
ask MAIL_FROM "Remetente" "${MAIL_FROM:-}"
ask MAIL_TO "Destinatários (separados por vírgula)" "${MAIL_TO:-}"
ask ATTACH_LOG "Anexar log ao email (true/false)" "${ATTACH_LOG:-true}"

NOTIFY_ON="${NOTIFY_ON:-all}"
def_notify_choice="1"
notify_mode_lower="$(echo "${NOTIFY_ON}" | tr '[:upper:]' '[:lower:]')"
case "$notify_mode_lower" in
    error|erro|failed|2) def_notify_choice="2" ;;
    success|sucesso|ok|3) def_notify_choice="3" ;;
    *) def_notify_choice="1" ;;
esac

echo
echo "Notificação por email (condição de envio):"
echo "  1 = Erro e Sucesso (notificar sempre)"
echo "  2 = Apenas Erro (notificar só se falhar)"
echo "  3 = Apenas Sucesso (notificar só se tiver sucesso)"
read -r -p "Escolha [${def_notify_choice}]: " notify_choice
notify_choice="${notify_choice:-$def_notify_choice}"
notify_choice_lower="$(echo "${notify_choice}" | tr '[:upper:]' '[:lower:]')"
case "$notify_choice_lower" in
    2|error|erro|failed) NOTIFY_ON="error" ;;
    3|success|sucesso|ok) NOTIFY_ON="success" ;;
    1|all|both|ambos|*) NOTIFY_ON="all" ;;
esac

echo
echo "Espaço em disco / Partições a reportar no email:"
IFS=',' read -r -a current_parts <<< "${MONITORED_PARTITIONS:-}"
if [[ ${#current_parts[@]} -eq 0 || -z "${current_parts[0]:-}" ]]; then
    read -r -a current_parts <<< "${MONITORED_PARTITIONS:-}"
fi
def_part_count="${#current_parts[@]}"
[[ -z "${current_parts[0]:-}" ]] && def_part_count=0

read -r -p "Quantas partições pretende monitorizar? [${def_part_count}]: " input_count
part_count="${input_count:-$def_part_count}"

new_parts=()
if [[ "$part_count" =~ ^[0-9]+$ ]] && (( part_count > 0 )); then
    for (( i=1; i<=part_count; i++ )); do
        idx=$(( i - 1 ))
        def_p="${current_parts[$idx]:-}"
        while true; do
            read -r -p "  Caminho da partição ${i}${def_p:+ [${def_p}]}: " p_val
            p_val="${p_val:-$def_p}"
            p_val="$(echo "$p_val" | xargs)"
            if [[ -n "$p_val" ]]; then
                if [[ ! -d "$p_val" ]]; then
                    echo "  Aviso: O caminho '${p_val}' não existe atualmente neste servidor."
                fi
                new_parts+=("$p_val")
                break
            else
                echo "  O caminho não pode estar vazio."
            fi
        done
    done
fi
MONITORED_PARTITIONS="$(IFS=','; echo "${new_parts[*]}")"

echo
echo "Alerta de espaço em disco (força notificação mesmo em 'Apenas Erro'):"
echo "  Se qualquer partição monitorizada atingir ou exceder este valor percentual,"
echo "  é sempre enviada uma notificação a alertar sobre a ocupação em disco."
def_alert_threshold="${PARTITION_ALERT_THRESHOLD:-80}"
read -r -p "Percentagem limite para emitir alerta de disco [${def_alert_threshold}]: " input_alert_threshold
PARTITION_ALERT_THRESHOLD="${input_alert_threshold:-$def_alert_threshold}"

echo
echo "Limpeza automática de espaço da partição RMAN:"
echo "  Caso a ocupação ultrapasse o limite percentual configurado,"
echo "  é executada a limpeza automática via rlrman com o utilizador oracle."
def_threshold="${RMAN_CLEANUP_THRESHOLD:-90}"
read -r -p "Percentagem limite de ocupação para acionar limpeza [${def_threshold}]: " input_threshold
RMAN_CLEANUP_THRESHOLD="${input_threshold:-$def_threshold}"

def_rman_part="${RMAN_PARTITION:-}"
if [[ -z "$def_rman_part" ]]; then
    for p in "${new_parts[@]}"; do
        if [[ "$p" =~ [rR][mM][aA][nN] ]]; then
            def_rman_part="$p"
            break
        fi
    done
    if [[ -z "$def_rman_part" && "${RMAN_LOG_DIR:-}" =~ [rR][mM][aA][nN] ]]; then
        def_rman_part="${RMAN_LOG_DIR}"
    fi
fi
read -r -p "Partição/caminho do RMAN a verificar${def_rman_part:+ [${def_rman_part}]}: " input_rman_part
RMAN_PARTITION="${input_rman_part:-$def_rman_part}"
if [[ -n "$RMAN_PARTITION" && ! -d "$RMAN_PARTITION" ]]; then
    echo "  Aviso: O caminho '${RMAN_PARTITION}' não existe atualmente neste servidor."
fi

SMTP_PASSWORD_ENC="${SMTP_PASSWORD_ENC:-}"
if [[ -n "$SMTP_USERNAME" ]]; then
    read -r -s -p "Password SMTP (vazio = manter atual): " pw; echo
    if [[ -n "$pw" ]]; then
        SMTP_PASSWORD_ENC=$(printf '%s' "$pw" | openssl enc -aes-256-cbc -pbkdf2 -salt -a -A -pass "file:${KEY_FILE}")
    fi
    unset pw
else
    SMTP_PASSWORD_ENC=""
fi

for v in ORACLE_SID RMAN_LOG_DIR SMTP_SERVER SMTP_PORT SMTP_USE_TLS SMTP_TLS_VERIFY SMTP_USERNAME MAIL_FROM MAIL_TO ATTACH_LOG NOTIFY_ON MONITORED_PARTITIONS PARTITION_ALERT_THRESHOLD RMAN_CLEANUP_THRESHOLD RMAN_PARTITION SMTP_PASSWORD_ENC; do
    [[ "${!v}" == *[\"\$\`\\]* ]] && { echo "Carateres inválidos em ${v}." >&2; exit 1; }
done

{
    echo "# Gerado por setup_smtp.sh em $(date '+%Y-%m-%d %H:%M:%S'). Password cifrada com config/smtp.key."
    for v in ORACLE_SID RMAN_LOG_DIR SMTP_SERVER SMTP_PORT SMTP_USE_TLS SMTP_TLS_VERIFY SMTP_USERNAME MAIL_FROM MAIL_TO ATTACH_LOG NOTIFY_ON MONITORED_PARTITIONS PARTITION_ALERT_THRESHOLD RMAN_CLEANUP_THRESHOLD RMAN_PARTITION SMTP_PASSWORD_ENC; do
        printf '%s="%s"\n' "$v" "${!v}"
    done
} > "$CONF_FILE"
chmod 600 -- "$CONF_FILE"
echo "Configuração gravada em ${CONF_FILE}"
