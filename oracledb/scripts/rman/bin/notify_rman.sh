#!/usr/bin/env bash
# Lê a última linha da tabela de estado e envia o email SMTP correspondente.
set -uo pipefail
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_HOME="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
for f in rman_monitor.env rman_monitor.conf; do
    [[ -r "${MONITOR_HOME}/config/$f" ]] && { set -a; source "${MONITOR_HOME}/config/$f"; set +a; }
done
STATUS_FILE="${STATUS_FILE:-${MONITOR_HOME}/logs/rman_status.txt}"
KEY_FILE="${KEY_FILE:-${MONITOR_HOME}/config/smtp.key}"

[[ -s "$STATUS_FILE" ]] || { echo "Tabela de estado vazia: $STATUS_FILE" >&2; exit 1; }
IFS='|' read -r ts host sid status log_path detail < <(tail -n 1 -- "$STATUS_FILE")

# Verificar alertas de espaço em disco em qualquer partição monitorizada (predefinição: >= 80%)
ALERT_THRESHOLD="${PARTITION_ALERT_THRESHOLD:-80}"
alert_messages=()
first_alert_part=""
first_alert_pct=0

if [[ -n "$ALERT_THRESHOLD" && "$ALERT_THRESHOLD" != "0" && ! "$ALERT_THRESHOLD" =~ ^(off|disabled|nao|no)$ ]]; then
    targets=()
    if [[ -n "${MONITORED_PARTITIONS:-}" ]]; then
        IFS=',' read -r -a _p_arr <<< "${MONITORED_PARTITIONS}"
        for _p in "${_p_arr[@]}"; do
            _p="$(echo "$_p" | xargs)"
            [[ -n "$_p" ]] && targets+=("$_p")
        done
    fi
    if [[ -n "${RMAN_PARTITION:-}" ]]; then
        targets+=("${RMAN_PARTITION}")
    fi
    if [[ ${#targets[@]} -eq 0 && -n "${RMAN_LOG_DIR:-}" ]]; then
        targets+=("${RMAN_LOG_DIR}")
    fi

    seen_mounts=()
    for t in "${targets[@]}"; do
        [[ -e "$t" ]] || continue
        cap_info="$(df -Pk "$t" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5, $6}')"
        if [[ -n "$cap_info" ]]; then
            read -r u_pct m_pt <<< "$cap_info"
            if [[ " ${seen_mounts[*]} " =~ " ${m_pt} " ]]; then
                continue
            fi
            seen_mounts+=("$m_pt")
            if [[ "$u_pct" =~ ^[0-9]+$ ]] && (( u_pct >= ALERT_THRESHOLD )); then
                alert_messages+=("  - ${t} (${m_pt}): ${u_pct}% ocupado (limite de alerta: ${ALERT_THRESHOLD}%)")
                if [[ -z "$first_alert_part" ]]; then
                    first_alert_part="${t}"
                    first_alert_pct="${u_pct}"
                fi
            fi
        fi
    done
fi

# Verificar política de envio de notificação (all = erro e sucesso, error = apenas erro, success = apenas sucesso)
NOTIFY_ON="${NOTIFY_ON:-all}"
notify_mode_lower="$(echo "${NOTIFY_ON}" | tr '[:upper:]' '[:lower:]')"
case "$notify_mode_lower" in
    error|erro|failed)
        if [[ "$status" == "SUCCESS" && ${#alert_messages[@]} -eq 0 ]]; then
            echo "$(date '+%Y-%m-%dT%H:%M:%S%z') notificação ignorada por filtro NOTIFY_ON=${NOTIFY_ON} (status=${status}, sem alertas de disco >= ${ALERT_THRESHOLD}%)" >> "${EXEC_LOG:-${MONITOR_HOME}/logs/monitor_execution.log}" 2>/dev/null || true
            exit 0
        fi
        if [[ "$status" == "SUCCESS" && ${#alert_messages[@]} -gt 0 ]]; then
            echo "$(date '+%Y-%m-%dT%H:%M:%S%z') notificação de sucesso forçada por alerta de partição >= ${ALERT_THRESHOLD}%" >> "${EXEC_LOG:-${MONITOR_HOME}/logs/monitor_execution.log}" 2>/dev/null || true
        fi
        ;;
    success|sucesso|ok)
        if [[ "$status" != "SUCCESS" ]]; then
            echo "$(date '+%Y-%m-%dT%H:%M:%S%z') notificação ignorada por filtro NOTIFY_ON=${NOTIFY_ON} (status=${status})" >> "${EXEC_LOG:-${MONITOR_HOME}/logs/monitor_execution.log}" 2>/dev/null || true
            exit 0
        fi
        ;;
    all|both|ambos|*)
        # Enviar em todas as situações (erro e sucesso)
        ;;
esac

if [[ -n "${SMTP_PASSWORD_ENC:-}" ]]; then
    SMTP_PASSWORD="$(printf '%s' "$SMTP_PASSWORD_ENC" | openssl enc -d -aes-256-cbc -pbkdf2 -a -A -pass "file:${KEY_FILE}")" \
        || { echo "Falha ao decifrar a password SMTP." >&2; exit 1; }
    export SMTP_PASSWORD
fi

CLEANUP_STATE_FILE="${MONITOR_HOME}/logs/last_cleanup.env"
CLEANUP_LOG_TO_ATTACH=""
CLEANUP_NOTICE=""

if [[ -r "$CLEANUP_STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CLEANUP_STATE_FILE"
    if [[ "${CLEANUP_EXECUTED:-false}" == "true" && -n "${CLEANUP_LOG_PATH:-}" && -f "${CLEANUP_LOG_PATH}" ]]; then
        CLEANUP_LOG_TO_ATTACH="${CLEANUP_LOG_PATH}"
        CLEANUP_NOTICE="

----------------------------------------------------------------------
AVISO: Limpeza automática de espaço RMAN executada
----------------------------------------------------------------------
A partição '${CLEANUP_PARTITION:-}' (${CLEANUP_MOUNT:-}) atingiu ${CLEANUP_USAGE_BEFORE:-}% de ocupação (limite: ${CLEANUP_THRESHOLD:-90}%).
Foi executada a limpeza de backups e archivelogs com mais de 7 dias com o utilizador oracle via rlrman.
Ocupação atual pós-limpeza: ${CLEANUP_USAGE_AFTER:-}%
O output detalhado da execução foi guardado em ficheiro e segue em anexo: $(basename "${CLEANUP_LOG_PATH}")"
    fi
fi

PARTITION_ALERT_NOTICE=""
if [[ ${#alert_messages[@]} -gt 0 ]]; then
    PARTITION_ALERT_NOTICE="

----------------------------------------------------------------------
ALERTA: Espaço em Disco >= ${ALERT_THRESHOLD}%
----------------------------------------------------------------------
Atenção: A(s) seguinte(s) partição(ões) atingiu(ram) ou ultrapassou(aram) o limite de alerta de ${ALERT_THRESHOLD}%:
$(printf '%s\n' "${alert_messages[@]}")"
fi

day="${ts%%T*}"
if [[ "$status" == SUCCESS ]]; then
    if [[ ${#alert_messages[@]} -gt 0 ]]; then
        subject="${host} - Backup RMAN OK - ALERTA: Partição ${first_alert_part} atingiu ${first_alert_pct}%"
    else
        subject="${host} - Backup RMAN OK"
    fi
    body="Servidor: ${host}
ORACLE_SID: ${sid}

O backup de rman do dia ${day} correu com sucesso.
Log: ${log_path}${PARTITION_ALERT_NOTICE}${CLEANUP_NOTICE}"
else
    if [[ ${#alert_messages[@]} -gt 0 ]]; then
        subject="${host} - ERRO no Backup RMAN - ALERTA: Partição ${first_alert_part} atingiu ${first_alert_pct}%"
    else
        subject="${host} - ERRO no Backup RMAN"
    fi
    body="Servidor: ${host}
ORACLE_SID: ${sid}

O backup de rman do dia ${day} falhou.
Detalhe: ${detail}
Log: ${log_path}${PARTITION_ALERT_NOTICE}${CLEANUP_NOTICE}"
fi

MSG_SUBJECT="$subject" MSG_BODY="$body" MSG_LOG="$log_path" CLEANUP_LOG="$CLEANUP_LOG_TO_ATTACH" MONITORED_PARTITIONS="${MONITORED_PARTITIONS:-}" python3 - <<'PY'
import os, smtplib, ssl, mimetypes, shutil
from email.message import EmailMessage

server = os.environ.get("SMTP_SERVER", "")
sender = os.environ.get("MAIL_FROM", "")
rcpt = [r.strip() for r in os.environ.get("MAIL_TO", "").split(",") if r.strip()]
if not server or not sender or not rcpt:
    raise SystemExit("SMTP_SERVER, MAIL_FROM e MAIL_TO têm de estar configurados")

body = os.environ["MSG_BODY"]

partitions_str = os.environ.get("MONITORED_PARTITIONS", "").strip()
if partitions_str:
    raw_parts = [p.strip() for p in partitions_str.replace(";", ",").split(",") if p.strip()]
    if not raw_parts and " " in partitions_str:
        raw_parts = [p.strip() for p in partitions_str.split() if p.strip()]
    if raw_parts:
        max_w = max([len(p) for p in raw_parts] + [10])
        fmt_hdr = "%-" + str(max_w) + "s  %11s  %11s  %9s"
        fmt_row = "%-" + str(max_w) + "s  %8.1f GB  %8.1f GB  %7.1f%%"
        fmt_err = "%-" + str(max_w) + "s  %35s"

        lines = []
        lines.append("")
        lines.append("Espaço em Disco / Partições:")
        lines.append(fmt_hdr % ("Partição", "Tamanho", "Disponível", "Ocupado"))
        lines.append("-" * (max_w + 37))
        for p in raw_parts:
            if os.path.exists(p):
                try:
                    usage = shutil.disk_usage(p)
                    total_gb = usage.total / (1024**3)
                    free_gb = usage.free / (1024**3)
                    used_pct = (usage.used / usage.total) * 100 if usage.total else 0
                    lines.append(fmt_row % (p, total_gb, free_gb, used_pct))
                except Exception:
                    lines.append(fmt_err % (p, "(erro de leitura)"))
            else:
                lines.append(fmt_err % (p, "(não encontrado)"))
        body += "\n" + "\n".join(lines)

m = EmailMessage()
m["Subject"] = os.environ["MSG_SUBJECT"]
m["From"] = sender
m["To"] = ", ".join(rcpt)
m.set_content(body)

log = os.environ.get("MSG_LOG", "")
if os.environ.get("ATTACH_LOG", "true").lower() in ("1", "true", "yes", "on") and os.path.isfile(log):
    with open(log, "rb") as fh:
        m.add_attachment(fh.read(), maintype="text", subtype="plain", filename=os.path.basename(log))

cleanup_log = os.environ.get("CLEANUP_LOG", "")
if cleanup_log and os.path.isfile(cleanup_log):
    with open(cleanup_log, "rb") as fh:
        m.add_attachment(fh.read(), maintype="text", subtype="plain", filename=os.path.basename(cleanup_log))

port = int(os.environ.get("SMTP_PORT", "25"))
s = smtplib.SMTP(server, port, timeout=20)
try:
    import sys
    s.ehlo()
    # STARTTLS: obrigatório se SMTP_USE_TLS=true; oportunista se houver autenticação e o servidor o oferecer
    want_tls = os.environ.get("SMTP_USE_TLS", "false").lower() in ("1", "true", "yes", "on")
    if want_tls or (os.environ.get("SMTP_USERNAME") and s.has_extn("starttls")):
        want_verify = os.environ.get("SMTP_TLS_VERIFY", "false").lower() not in ("0", "false", "no", "off")
        ctx = ssl.create_default_context()
        if not want_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            s.starttls(context=ctx)
            s.ehlo()
        except ssl.SSLError as exc:
            print("AVISO: Validação do certificado TLS falhou (%s)." % exc, file=sys.stderr)
            print("A tentar novamente com verificação de certificado desativada...", file=sys.stderr)
            try:
                s.close()
            except Exception:
                pass
            s = smtplib.SMTP(server, port, timeout=20)
            s.ehlo()
            ctx_no_verify = ssl.create_default_context()
            ctx_no_verify.check_hostname = False
            ctx_no_verify.verify_mode = ssl.CERT_NONE
            s.starttls(context=ctx_no_verify)
            s.ehlo()
    if os.environ.get("SMTP_USERNAME"):
        if s.has_extn("auth"):
            try:
                s.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
            except smtplib.SMTPException as exc:
                print("AVISO: autenticação SMTP falhou (%s). Mecanismos anunciados: %s. A tentar enviar sem autenticação."
                      % (exc, s.esmtp_features.get("auth", "")), file=sys.stderr)
        else:
            print("AVISO: o servidor SMTP não anuncia AUTH (ative STARTTLS ou limpe o utilizador); a enviar sem autenticação.", file=sys.stderr)
    s.send_message(m)
finally:
    try:
        s.quit()
    except Exception:
        pass
PY
rc=$?
if [[ $rc -eq 0 ]]; then
    rm -f "$CLEANUP_STATE_FILE" 2>/dev/null || true
fi
echo "$(date '+%Y-%m-%dT%H:%M:%S%z') notificação ${status} (rc=${rc})" >> "${EXEC_LOG:-${MONITOR_HOME}/logs/monitor_execution.log}" 2>/dev/null || true
exit $rc
