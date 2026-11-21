#!/bin/bash
# =============================================================================
# docker-cleanup.sh - Limpeza periodica do Docker no Synology
#
# Limpa:   imagens sem uso, build cache, redes sem uso, logs grandes
# NUNCA:   containers (parados ou a correr) nem volumes
#
# Uso:     sudo /volume1/scripts/docker-cleanup.sh
# Agendar: Painel de Controlo > Agendador de tarefas > Script definido pelo
#          utilizador > Utilizador: root
# =============================================================================

# ---- Configuracao ----------------------------------------------------------
DOCKER="/usr/local/bin/docker"
DOCKER_ROOT="/volume1/@docker"
IDADE_MIN="168h"           # so limpa imagens/cache/redes com mais de 7 dias
LOG_MAX_MB=100             # trunca logs de containers acima deste tamanho
LOG_DIR="/volume1/scripts/logs"
LOG_FILE="$LOG_DIR/docker-cleanup.log"
LOG_KEEP_LINES=2000        # tamanho maximo do log deste script
TIMEOUT=600                # segundos maximos por operacao
DRY_RUN=0                  # 1 = so mostra o que faria (ou usar --dry-run)

[[ "$1" == "--dry-run" ]] && DRY_RUN=1

# ---- Funcoes ---------------------------------------------------------------
mkdir -p "$LOG_DIR"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" | tee -a "$LOG_FILE"; }

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[DRY-RUN] $*"
    else
        timeout "$TIMEOUT" "$@" 2>&1 | grep -E "Total reclaimed|Deleted Networks|^Error" | while read -r l; do log "  $l"; done
    fi
}

espaco() {
    timeout 120 "$DOCKER" system df 2>/dev/null \
        || du -sh "$DOCKER_ROOT" 2>/dev/null \
        || echo "(nao foi possivel calcular)"
}

# ---- Pre-verificacoes ------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "Tem de correr como root (sudo)."; exit 1
fi

exec 9>/tmp/docker-cleanup.lock
if ! flock -n 9; then
    log "Ja existe uma limpeza a decorrer. A sair."; exit 0
fi

if ! timeout 30 "$DOCKER" info >/dev/null 2>&1; then
    log "ERRO: daemon Docker nao responde. A sair."; exit 1
fi

# ---- Execucao --------------------------------------------------------------
log "===== Inicio da limpeza (dry-run=$DRY_RUN) ====="
log "Espaco antes:"; espaco | while read -r l; do log "  $l"; done

N_ANTES=$("$DOCKER" ps -aq | wc -l)

# 1. Imagens sem nenhum container associado (nem parado) e com mais de 7 dias
log "1/4 Imagens sem uso (> $IDADE_MIN)"
run "$DOCKER" image prune -a -f --filter "until=$IDADE_MIN"

# 2. Build cache
log "2/4 Build cache (> $IDADE_MIN)"
run "$DOCKER" builder prune -a -f --filter "until=$IDADE_MIN"

# 3. Redes sem nenhum container associado (as dos containers parados ficam)
log "3/4 Redes sem uso (> $IDADE_MIN)"
run "$DOCKER" network prune -f --filter "until=$IDADE_MIN"

# 4. Logs de containers demasiado grandes (trunca, nao apaga)
log "4/4 Logs de containers > ${LOG_MAX_MB}MB"
find "$DOCKER_ROOT/containers" -name "*-json.log" -size +"${LOG_MAX_MB}M" 2>/dev/null | while read -r f; do
    id=$(basename "$(dirname "$f")")
    nome=$("$DOCKER" inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##')
    tam=$(du -h "$f" | cut -f1)
    if [[ $DRY_RUN -eq 1 ]]; then
        log "  [DRY-RUN] truncaria ${nome:-$id} ($tam)"
    else
        truncate -s 0 "$f" && log "  Truncado ${nome:-$id} ($tam)"
    fi
done

# ---- Verificacao de seguranca ----------------------------------------------
N_DEPOIS=$("$DOCKER" ps -aq | wc -l)
if [[ $N_ANTES -ne $N_DEPOIS ]]; then
    log "AVISO: numero de containers mudou ($N_ANTES -> $N_DEPOIS) durante a execucao!"
else
    log "Containers intactos: $N_DEPOIS"
fi

log "Espaco depois:"; espaco | while read -r l; do log "  $l"; done
log "===== Fim ====="

# Rodar o log deste script
tail -n "$LOG_KEEP_LINES" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
