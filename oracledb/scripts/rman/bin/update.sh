#!/usr/bin/env bash
# Atualiza a estrutura do monitor após uma nova versão do projeto:
# cria pastas/ficheiros em falta, migra o .conf, acerta permissões. Não apaga dados.
# Uso: update.sh
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${HOME_DIR}/config"
CONF="${CONFIG_DIR}/rman_monitor.conf"
VERSION="$(cat "${HOME_DIR}/VERSION" 2>/dev/null || echo desconhecida)"
INSTALLED_FILE="${CONFIG_DIR}/.installed_version"
INSTALLED="$(cat "$INSTALLED_FILE" 2>/dev/null || echo nenhuma)"

echo "Versão instalada: ${INSTALLED} -> versão do projeto: ${VERSION}"

mkdir -p -- "${HOME_DIR}/logs" "$CONFIG_DIR"
chmod 750 -- "${HOME_DIR}/logs"
chmod +x -- "${SCRIPT_DIR}"/*.sh

# Listas de padrões em falta
[[ -f "${CONFIG_DIR}/tipos_error.txt" ]] || { : > "${CONFIG_DIR}/tipos_error.txt"; echo "Criado tipos_error.txt (vazio)"; }
if [[ ! -f "${CONFIG_DIR}/tipos_success.txt" ]]; then
    echo "Recovery Manager complete" > "${CONFIG_DIR}/tipos_success.txt"
    echo "Criado tipos_success.txt"
fi
if [[ ! -f "${CONFIG_DIR}/rman_cleanup.cmd" ]]; then
    cat <<'EOF' > "${CONFIG_DIR}/rman_cleanup.cmd"
CROSSCHECK BACKUP;
DELETE NOPROMPT BACKUP COMPLETED BEFORE 'SYSDATE-7';
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'sysdate-7';
EXIT;
EOF
    echo "Criado rman_cleanup.cmd"
fi

# Migração do .conf
if [[ -f "$CONF" ]]; then
    # Substitui o antigo SUCCESS_PATTERN pela lista tipos_success.txt
    if grep -q '^SUCCESS_PATTERN=' "$CONF"; then
        old=$(grep -m1 '^SUCCESS_PATTERN=' "$CONF" | cut -d= -f2- | tr -d '"')
        [[ -n "$old" ]] && ! grep -qxF -- "$old" "${CONFIG_DIR}/tipos_success.txt" && echo "$old" >> "${CONFIG_DIR}/tipos_success.txt"
        sed -i '/^SUCCESS_PATTERN=/d' "$CONF"
        echo "Migrado SUCCESS_PATTERN para tipos_success.txt"
    fi
    if ! grep -q '^RMAN_LOG_DIR=' "$CONF"; then
        echo 'RMAN_LOG_DIR=""' >> "$CONF"
        echo "AVISO: RMAN_LOG_DIR em falta no .conf; execute bin/setup_smtp.sh para o definir."
    fi
    if ! grep -q '^NOTIFY_ON=' "$CONF"; then
        echo 'NOTIFY_ON="all"' >> "$CONF"
        echo "Adicionado NOTIFY_ON=\"all\" ao .conf (predefinição: erro e sucesso)."
    fi
    if ! grep -q '^SMTP_TLS_VERIFY=' "$CONF"; then
        echo 'SMTP_TLS_VERIFY="false"' >> "$CONF"
        echo "Adicionado SMTP_TLS_VERIFY=\"false\" ao .conf (predefinição: compatível com CA privada/interna)."
    fi
    if ! grep -q '^MONITORED_PARTITIONS=' "$CONF"; then
        echo 'MONITORED_PARTITIONS=""' >> "$CONF"
        echo "Adicionado MONITORED_PARTITIONS=\"\" ao .conf."
    fi
    if ! grep -q '^PARTITION_ALERT_THRESHOLD=' "$CONF"; then
        echo 'PARTITION_ALERT_THRESHOLD="80"' >> "$CONF"
        echo "Adicionado PARTITION_ALERT_THRESHOLD=\"80\" ao .conf (predefinição: alerta se >= 80%)."
    fi
    if ! grep -q '^RMAN_CLEANUP_THRESHOLD=' "$CONF"; then
        echo 'RMAN_CLEANUP_THRESHOLD="90"' >> "$CONF"
        echo "Adicionado RMAN_CLEANUP_THRESHOLD=\"90\" ao .conf."
    fi
    if ! grep -q '^RMAN_PARTITION=' "$CONF"; then
        echo 'RMAN_PARTITION=""' >> "$CONF"
        echo "Adicionado RMAN_PARTITION=\"\" ao .conf."
    fi
    chmod 600 -- "$CONF"
else
    echo "AVISO: ${CONF} não existe; execute bin/setup_smtp.sh."
fi
[[ -f "${CONFIG_DIR}/smtp.key" ]] && chmod 600 -- "${CONFIG_DIR}/smtp.key"

echo "$VERSION" > "$INSTALLED_FILE"
echo "Estrutura atualizada."
