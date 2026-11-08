#!/usr/bin/env bash
# Copia a pasta config de uma instalação antiga do monitor para a nova versão.
# Pede o caminho antigo e o novo (ou recebe-os como $1 e $2).
# Os ficheiros já existentes no destino são guardados em config.bak-<data>.
set -uo pipefail

old="${1:-}"; new="${2:-}"
[[ -n "$old" ]] || read -r -e -p "Caminho do monitor antigo (ex.: /opt/oracle/monitor_old): " old
[[ -n "$new" ]] || read -r -e -p "Caminho do monitor novo   (ex.: /opt/oracle/monitor_new): " new

old="${old%/}"; new="${new%/}"
[[ -d "${old}/config" ]] || { echo "Não existe ${old}/config" >&2; exit 1; }
[[ -d "$new" ]] || { echo "Não existe o diretório novo: ${new}" >&2; exit 1; }
old="$(cd -- "$old" && pwd -P)"; new="$(cd -- "$new" && pwd -P)"
[[ "$old" != "$new" ]] || { echo "Os caminhos antigo e novo são iguais." >&2; exit 1; }

echo "Copiar ${old}/config  ->  ${new}/config"
read -r -p "Confirmar? (s/N): " ok
[[ "$ok" =~ ^[sSyY]$ ]] || { echo "Cancelado."; exit 0; }

if [[ -d "${new}/config" ]] && [[ -n "$(ls -A -- "${new}/config" 2>/dev/null)" ]]; then
    bak="${new}/config.bak-$(date +%Y%m%d%H%M%S)"
    cp -a -- "${new}/config" "$bak" && echo "Cópia de segurança do config novo: ${bak}"
fi

mkdir -p -- "${new}/config"
cp -a -- "${old}/config/." "${new}/config/" || { echo "Falha na cópia." >&2; exit 1; }
chmod 700 -- "${new}/config"
for f in smtp.key rman_monitor.conf; do
    [[ -f "${new}/config/$f" ]] && chmod 600 -- "${new}/config/$f"
done
echo "Configuração copiada."

# Opcional: manter o histórico de estado
if [[ -f "${old}/logs/rman_status.txt" ]]; then
    read -r -p "Copiar também a tabela de estado logs/rman_status.txt? (s/N): " h
    if [[ "$h" =~ ^[sSyY]$ ]]; then
        mkdir -p -- "${new}/logs" && cp -a -- "${old}/logs/rman_status.txt" "${new}/logs/" && echo "Tabela de estado copiada."
    fi
fi

# Ajusta a estrutura à versão nova
if [[ -x "${new}/bin/update.sh" ]]; then
    "${new}/bin/update.sh"
else
    echo "AVISO: ${new}/bin/update.sh não encontrado; execute-o manualmente."
fi
