#!/usr/bin/env bash
# =====================================================================
#  gestao_logs.sh - Gestão e limpeza automática de logs
#                   (alert.log, listener.log, *.trc, *.aud, ...)
# ---------------------------------------------------------------------
#  Uso:
#    ./gestao_logs.sh              Assistente interativo (fundo azul)
#    ./gestao_logs.sh --executar   Executa a limpeza (para o cron)
#    ./gestao_logs.sh --simular    Mostra o que seria feito, sem alterar nada
#    ./gestao_logs.sh --ajuda      Ajuda
#
#  Ficheiros (na mesma pasta do script, ou em $GESTAO_LOGS_HOME):
#    gestao_logs.conf        Configuração (formato tipo .env)
#    historico/AAAA-MM-DD.log  Histórico diário das ações (365 dias)
#    gestao_logs.cron        Linha de crontab gerada
#    gestao_logs.logrotate   Configuração de logrotate gerada
#    ultima_execucao.log     Saída da última execução pelo cron
#
#  Compatível com bash 4.2+ (Oracle Linux / RHEL 7, 8, 9).
# =====================================================================

set -o pipefail

VERSAO="1.0"
SCRIPT_PATH="$(readlink -f "$0")"
BASE_DIR="${GESTAO_LOGS_HOME:-$(dirname "$SCRIPT_PATH")}"
CONF_FILE="$BASE_DIR/gestao_logs.conf"
ESTADO_FILE="$BASE_DIR/.gestao_logs.estado"
HIST_DIR="$BASE_DIR/historico"
LOCK_FILE="$BASE_DIR/.gestao_logs.lock"
CRON_FILE="$BASE_DIR/gestao_logs.cron"
LOGROTATE_FILE="$BASE_DIR/gestao_logs.logrotate"
ULTIMA_EXEC="$BASE_DIR/ultima_execucao.log"
BACKTITLE="Gestão de Logs v$VERSAO   |   $(hostname -s 2>/dev/null)   |   utilizador: $(id -un)"

HIST_DIAS=365
ENTRADAS=()
SAIDA_ECRA=1
TOTAL_BYTES=0; TOTAL_FICH=0; TOTAL_ERROS=0

# Pastas que nunca podem ser geridas (comparação exata)
PROIBIDOS=(/ /bin /boot /dev /etc /lib /lib64 /proc /root /sbin /sys /usr /var /home /opt /run)

# =====================================================================
#  Funções utilitárias
# =====================================================================

# 1G / 500M / 100K / 1GB -> bytes ; devolve -1 se inválido
para_bytes() {
    local v="${1^^}"; v="${v%B}"
    [[ $v =~ ^([0-9]+)([KMGT]?)$ ]] || { echo -1; return 1; }
    local n=${BASH_REMATCH[1]} u=${BASH_REMATCH[2]}
    case $u in
        K) n=$(( n * 1024 )) ;;
        M) n=$(( n * 1024 * 1024 )) ;;
        G) n=$(( n * 1024 * 1024 * 1024 )) ;;
        T) n=$(( n * 1024 * 1024 * 1024 * 1024 )) ;;
    esac
    echo "$n"
}

# bytes -> 1.5G
humano() {
    awk -v b="${1:-0}" 'BEGIN{split("B K M G T",u," ");i=1;
        while(b>=1024&&i<5){b/=1024;i++}
        if(i==1) printf "%dB",b; else printf "%.1f%s",b,u[i]}'
}

descr_acao() {
    case $1 in
        rodar)    echo "rodar (guarda cópia .gz e esvazia)" ;;
        esvaziar) echo "esvaziar (truncate, sem cópia)" ;;
        apagar)   echo "apagar ficheiros" ;;
        *)        echo "$1" ;;
    esac
}

descr_limites() { # acao maxsz maxd dcop
    local t d c=""
    [[ $2 == 0 ]] && t="sem limite" || t="$2"
    if [[ $3 == 0 ]]; then d="sem limite"
    elif [[ $1 == apagar ]]; then d="ficheiros com mais de $3 dias"
    else d="a cada $3 dias"; fi
    [[ $1 == rodar ]] && c=" | cópias .gz: $4 dias"
    echo "tamanho: $t | tempo: $d$c"
}

# =====================================================================
#  Configuração (formato tipo .env)
# =====================================================================

carregar_conf() {
    ENTRADAS=()
    HIST_DIAS=365
    [[ -f $CONF_FILE ]] || return 0
    local linha chave valor
    while IFS= read -r linha || [[ -n $linha ]]; do
        linha="${linha%$'\r'}"
        [[ -z $linha || $linha == \#* ]] && continue
        chave="${linha%%=*}"; valor="${linha#*=}"
        case $chave in
            HIST_DIAS) [[ $valor =~ ^[0-9]+$ ]] && HIST_DIAS=$valor ;;
            ENTRADA)   ENTRADAS+=("$valor") ;;
        esac
    done < "$CONF_FILE"
}

guardar_conf() {
    mkdir -p "$BASE_DIR"
    local tmp="$CONF_FILE.tmp.$$" e
    {
        echo "# ===================================================================="
        echo "# gestao_logs.conf - gerado por gestao_logs.sh em $(date '+%Y-%m-%d %H:%M:%S')"
        echo "# Pode ser editado à mão. Uma linha ENTRADA= por caminho."
        echo "#"
        echo "# ENTRADA=caminho|padrao|subpastas(S/N)|acao|max_tamanho|max_dias|dias_copias|ativo(S/N)"
        echo "#   acao        : rodar | esvaziar | apagar"
        echo "#   max_tamanho : 1G, 500M, 100K ... (0 = sem limite)"
        echo "#   max_dias    : apagar         -> apaga ficheiros não modificados há mais de N dias"
        echo "#                 esvaziar/rodar -> limpa se a última limpeza foi há N dias ou mais"
        echo "#                 (0 = sem limite)"
        echo "#   dias_copias : só para 'rodar' - dias a manter as cópias .gz"
        echo "# ===================================================================="
        echo
        echo "# Dias de histórico a manter"
        echo "HIST_DIAS=$HIST_DIAS"
        echo
        for e in "${ENTRADAS[@]}"; do echo "ENTRADA=$e"; done
    } > "$tmp" && mv -f "$tmp" "$CONF_FILE" && chmod 640 "$CONF_FILE"
}

# =====================================================================
#  Estado (data da última limpeza de cada ficheiro) e histórico
# =====================================================================

estado_get() {
    [[ -f $ESTADO_FILE ]] || return 0
    awk -F'|' -v f="$1" '$1==f{v=$2} END{print v}' "$ESTADO_FILE"
}

estado_set() {
    local tmp="$ESTADO_FILE.tmp.$$"
    { [[ -f $ESTADO_FILE ]] && awk -F'|' -v f="$1" '$1!=f' "$ESTADO_FILE"
      echo "$1|$2"; } > "$tmp" && mv -f "$tmp" "$ESTADO_FILE"
}

# registar MODO ACAO FICHEIRO BYTES_LIBERTADOS MOTIVO RESULTADO
# Histórico: data|modo|acao|ficheiro|bytes|motivo|resultado
registar() {
    if [[ $1 == EXEC ]]; then
        mkdir -p "$HIST_DIR"
        echo "$(date '+%Y-%m-%d %H:%M:%S')|$1|$2|$3|$4|$5|$6" >> "$HIST_DIR/$(date +%Y-%m-%d).log"
    fi
    if [[ $SAIDA_ECRA == 1 ]]; then
        printf '%-9s %8s  %s\n' "$2" "$(humano "$4")" "$3"
        printf '          %s -> %s\n' "$5" "$6"
    fi
}

purgar_historico() {
    [[ -d $HIST_DIR ]] || return 0
    find "$HIST_DIR" -maxdepth 1 -type f -name '????-??-??.log' -mtime +"$HIST_DIAS" -delete 2>/dev/null
}

# =====================================================================
#  Motor de limpeza
# =====================================================================

tratar_ficheiro() { # modo f acao maxb maxsz maxd agora
    local modo=$1 f=$2 acao=$3 maxb=$4 maxsz=$5 maxd=$6 agora=$7
    local tam mt motivo="" ult res libertado dest gz

    tam=$(stat -c %s -- "$f" 2>/dev/null) || return 0
    mt=$(stat -c %Y -- "$f" 2>/dev/null) || return 0

    if [[ $acao == apagar ]]; then
        if (( maxb > 0 && tam >= maxb )); then
            motivo="tamanho $(humano "$tam") >= $maxsz"
        elif (( maxd > 0 && mt < agora - maxd * 86400 )); then
            motivo="sem alterações há $(( (agora - mt) / 86400 )) dias"
        fi
    else
        if (( maxd > 0 )); then
            ult=$(estado_get "$f")
            # primeira vez que o ficheiro é visto: começa a contar a partir de agora
            [[ -z $ult && $modo == EXEC ]] && estado_set "$f" "$agora"
        fi
        (( tam == 0 )) && return 0
        if (( maxb > 0 && tam >= maxb )); then
            motivo="tamanho $(humano "$tam") >= $maxsz"
        elif (( maxd > 0 )) && [[ -n $ult ]] && (( agora - ult + 3600 >= maxd * 86400 )); then
            motivo="última limpeza há $(( (agora - ult) / 86400 )) dias"
        fi
    fi

    [[ -z $motivo ]] && return 0

    if [[ $modo != EXEC ]]; then
        registar "$modo" "${acao^^}" "$f" "$tam" "$motivo" "SIMULADO"
        TOTAL_BYTES=$(( TOTAL_BYTES + tam )); TOTAL_FICH=$(( TOTAL_FICH + 1 ))
        return 0
    fi

    libertado=$tam
    case $acao in
        apagar)
            if rm -f -- "$f"; then res="OK"; else res="ERRO ao apagar"; fi ;;
        esvaziar)
            if : > "$f"; then res="OK"; else res="ERRO ao esvaziar"; fi ;;
        rodar)
            dest="$f.$(date +%Y%m%d_%H%M%S).gz"
            if gzip -c -- "$f" > "$dest" 2>/dev/null; then
                if : > "$f"; then
                    chown --reference="$f" "$dest" 2>/dev/null
                    chmod --reference="$f" "$dest" 2>/dev/null
                    gz=$(stat -c %s -- "$dest" 2>/dev/null || echo 0)
                    libertado=$(( tam - gz ))
                    res="OK, cópia: $(basename "$dest") ($(humano "$gz"))"
                else
                    res="ERRO ao esvaziar (cópia criada: $(basename "$dest"))"
                fi
            else
                rm -f -- "$dest"
                res="ERRO ao comprimir (espaço em disco?)"
            fi ;;
    esac

    if [[ $res == OK* ]]; then
        [[ $acao != apagar ]] && estado_set "$f" "$agora"
        TOTAL_BYTES=$(( TOTAL_BYTES + libertado )); TOTAL_FICH=$(( TOTAL_FICH + 1 ))
    else
        libertado=0; TOTAL_ERROS=$(( TOTAL_ERROS + 1 ))
    fi
    registar EXEC "${acao^^}" "$f" "$libertado" "$motivo" "$res"
}

processar() { # EXEC | SIMULACAO
    local modo=$1 agora e cam pad rec acao maxsz maxd dcop ativo maxb f
    local prof excl
    agora=$(date +%s)
    TOTAL_BYTES=0; TOTAL_FICH=0; TOTAL_ERROS=0

    for e in "${ENTRADAS[@]}"; do
        IFS='|' read -r cam pad rec acao maxsz maxd dcop ativo <<< "$e"
        [[ $ativo == S ]] || continue
        maxb=$(para_bytes "$maxsz"); (( maxb < 0 )) && maxb=0
        [[ $maxd =~ ^[0-9]+$ ]] || maxd=0
        [[ $dcop =~ ^[0-9]+$ ]] || dcop=0

        if [[ ! -d $cam ]]; then
            registar "$modo" AVISO "$cam" 0 "caminho não existe" "IGNORADO"
            continue
        fi

        prof=(-maxdepth 1); [[ $rec == S ]] && prof=()
        excl=();            [[ $acao != apagar ]] && excl=(! -name '*.gz')

        while IFS= read -r -d '' f; do
            tratar_ficheiro "$modo" "$f" "$acao" "$maxb" "$maxsz" "$maxd" "$agora"
        done < <(find "$cam" "${prof[@]}" -type f -name "$pad" "${excl[@]}" -print0 2>/dev/null)

        # rodar: apagar cópias .gz antigas
        if [[ $acao == rodar ]] && (( dcop > 0 )); then
            while IFS= read -r -d '' f; do
                local t; t=$(stat -c %s -- "$f" 2>/dev/null || echo 0)
                if [[ $modo == EXEC ]]; then
                    if rm -f -- "$f"; then
                        TOTAL_BYTES=$(( TOTAL_BYTES + t )); TOTAL_FICH=$(( TOTAL_FICH + 1 ))
                        registar EXEC APAGAR_GZ "$f" "$t" "cópia com mais de $dcop dias" "OK"
                    else
                        TOTAL_ERROS=$(( TOTAL_ERROS + 1 ))
                        registar EXEC APAGAR_GZ "$f" 0 "cópia com mais de $dcop dias" "ERRO"
                    fi
                else
                    TOTAL_BYTES=$(( TOTAL_BYTES + t )); TOTAL_FICH=$(( TOTAL_FICH + 1 ))
                    registar "$modo" APAGAR_GZ "$f" "$t" "cópia com mais de $dcop dias" "SIMULADO"
                fi
            done < <(find "$cam" "${prof[@]}" -type f -name "$pad.*.gz" -mtime +"$dcop" -print0 2>/dev/null)
        fi
    done
}

executar_limpeza() { # EXEC | SIMULACAO
    local modo=$1
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "ERRO: já existe uma execução em curso ($LOCK_FILE)."
        return 1
    fi
    carregar_conf
    echo "=================================================================="
    echo " Gestão de Logs - $( [[ $modo == EXEC ]] && echo EXECUÇÃO || echo SIMULAÇÃO ) - $(date '+%Y-%m-%d %H:%M:%S')"
    echo " Configuração: $CONF_FILE"
    echo "=================================================================="
    if (( ${#ENTRADAS[@]} == 0 )); then
        echo "Sem entradas configuradas."
        exec 9>&-; return 0
    fi
    processar "$modo"
    echo "------------------------------------------------------------------"
    printf ' Ficheiros tratados: %d  |  Espaço %s: %s  |  Erros: %d\n' \
        "$TOTAL_FICH" "$( [[ $modo == EXEC ]] && echo libertado || echo a libertar )" \
        "$(humano "$TOTAL_BYTES")" "$TOTAL_ERROS"
    if [[ $modo == EXEC ]]; then
        SAIDA_ECRA=0
        registar EXEC RESUMO "-" "$TOTAL_BYTES" "$TOTAL_FICH ficheiro(s), $TOTAL_ERROS erro(s)" "FIM"
        SAIDA_ECRA=1
        purgar_historico
    fi
    exec 9>&-
    (( TOTAL_ERROS == 0 ))
}

# =====================================================================
#  Interface (whiptail / dialog)
# =====================================================================

DLG=""
detetar_ui() {
    if command -v whiptail >/dev/null 2>&1; then DLG=whiptail
    elif command -v dialog >/dev/null 2>&1; then DLG=dialog
    else
        echo "ERRO: é necessário o whiptail (pacote 'newt') ou o dialog."
        echo "      Oracle Linux / RHEL: sudo yum install -y newt"
        echo "      Debian / Ubuntu    : sudo apt install -y whiptail"
        exit 1
    fi
    # fundo azul (estilo instalador)
    export NEWT_COLORS='root=white,blue'
    local l c
    l=$(tput lines 2>/dev/null || echo 24); c=$(tput cols 2>/dev/null || echo 80)
    H=$(( l - 4 )); (( H > 32 )) && H=32; (( H < 18 )) && H=18
    W=$(( c - 6 )); (( W > 120 )) && W=120; (( W < 70 )) && W=70
}

ui()   { "$DLG" --backtitle "$BACKTITLE" "$@" 3>&1 1>&2 2>&3; }
msg()  { ui --title "$1" --msgbox "$2" "${3:-12}" "${4:-74}"; }
sn()   { ui --title "$1" --yesno "$2" "${3:-12}" "${4:-74}"; }
sn_nao() { ui --title "$1" --defaultno --yesno "$2" "${3:-12}" "${4:-74}"; }
texto() {
    local extra=(); [[ $DLG == whiptail ]] && extra=(--scrolltext)
    ui --title "$1" "${extra[@]}" --textbox "$2" "$H" "$W"
}

boas_vindas() {
    msg "Bem-vindo" \
"Este assistente configura a gestão e limpeza automática de logs
(alert.log, listener.log, ficheiros de trace, audit, etc.).

Para cada caminho indica:
  - que ficheiros analisar   (ex.: alert_*.log, *.trc, listener.log)
  - o tamanho máximo         (ex.: 1G)  e/ou
  - o tempo máximo em dias
  - a ação: rodar (cópia .gz + esvaziar), esvaziar ou apagar

A limpeza corre todos os dias pelo cron (ou pelo logrotate),
sem ser preciso reiniciar a base de dados.

Configuração : $CONF_FILE
Histórico    : $HIST_DIR ($HIST_DIAS dias)" 22 76
}

# ---------------------------------------------------------------------
ocupacao() { # cam pad rec -> "N ficheiros, X"
    local prof=(-maxdepth 1); [[ $3 == S ]] && prof=()
    [[ -d $1 ]] || { echo "caminho não existe"; return; }
    find "$1" "${prof[@]}" -type f -name "$2" -printf '%s\n' 2>/dev/null |
        awk '{n++; s+=$1} END{
            split("B K M G T",u," "); i=1; while(s>=1024&&i<5){s/=1024;i++}
            printf "%d ficheiro(s), ", n; if(i==1) printf "%dB\n",s; else printf "%.1f%s\n",s,u[i]}'
}

ver_conf() {
    local tmp i cam pad rec acao maxsz maxd dcop ativo
    tmp=$(mktemp)
    {
        echo "Ficheiro de configuração: $CONF_FILE"
        echo "Histórico mantido: $HIST_DIAS dias"
        echo
        if (( ${#ENTRADAS[@]} == 0 )); then
            echo "Ainda não existem entradas. Use 'Adicionar caminho / ficheiro'."
        fi
        for i in "${!ENTRADAS[@]}"; do
            IFS='|' read -r cam pad rec acao maxsz maxd dcop ativo <<< "${ENTRADAS[$i]}"
            printf '[%d] %-8s %s\n' $(( i + 1 )) "$([[ $ativo == S ]] && echo ATIVO || echo INATIVO)" "$cam"
            printf '     Ficheiros : %s   (subpastas: %s)\n' "$pad" "$([[ $rec == S ]] && echo sim || echo não)"
            printf '     Ação      : %s\n' "$(descr_acao "$acao")"
            printf '     Limites   : %s\n' "$(descr_limites "$acao" "$maxsz" "$maxd" "$dcop")"
            printf '     Atual     : %s\n\n' "$(ocupacao "$cam" "$pad" "$rec")"
        done
    } > "$tmp"
    texto "Configuração atual" "$tmp"
    rm -f "$tmp"
}

# ---------------------------------------------------------------------
adicionar() {
    local cam pad rec="N" acao maxsz maxd dcop=0 p def_cam def_pad="alert_*.log" def_sz def_d txt_d e c2 p2

    def_cam="${ORACLE_BASE:-/oracle/app/oracle}/diag/rdbms/"
    while true; do
        cam=$(ui --title "Adicionar (1/6) - Caminho" --inputbox \
"Indique a pasta a analisar ou o caminho completo de um ficheiro.

Exemplos:
  /oracle/app/oracle/diag/rdbms/orcl/ORCL/trace
  /oracle/app/oracle/diag/rdbms/orcl/ORCL/trace/alert_ORCL.log
  /oracle/app/oracle/diag/tnslsnr/srv01/listener/trace

Dica: SELECT value FROM v\$diag_info WHERE name='Diag Trace';" 17 78 "$def_cam") || return
        cam="${cam%/}"; [[ -z $cam ]] && cam="/"
        def_cam="$cam"
        if [[ $cam != /* ]]; then msg "Caminho inválido" "O caminho tem de ser absoluto (começar por /)."; continue; fi
        for p in "${PROIBIDOS[@]}"; do
            if [[ $cam == "$p" ]]; then msg "Caminho não permitido" "Por segurança, não é permitido gerir a pasta '$cam'."; continue 2; fi
        done
        if [[ -f $cam ]]; then
            def_pad="$(basename "$cam")"; cam="$(dirname "$cam")"
        elif [[ ! -d $cam ]]; then
            sn_nao "Caminho não encontrado" "O caminho não existe neste servidor:\n\n$cam\n\nQuer adicioná-lo mesmo assim?" || continue
        fi
        break
    done

    while true; do
        pad=$(ui --title "Adicionar (2/6) - Ficheiros" --inputbox \
"Pasta: $cam

Nome ou padrão dos ficheiros a gerir (aceita * e ?):
  alert_*.log    alert log da base de dados
  listener.log   log do listener
  *.trc          ficheiros de trace
  *.trm          metadados de trace
  *.aud          audit files" 16 78 "$def_pad") || return
        if [[ -z $pad || $pad == */* ]]; then msg "Padrão inválido" "Indique só o nome ou padrão, sem '/'."; continue; fi
        break
    done

    for e in "${ENTRADAS[@]}"; do
        IFS='|' read -r c2 p2 _ <<< "$e"
        if [[ $c2 == "$cam" && $p2 == "$pad" ]]; then
            msg "Entrada duplicada" "Já existe uma entrada para:\n\n$cam/$pad\n\nRemova-a primeiro se a quiser alterar."
            return
        fi
    done

    sn_nao "Adicionar (3/6) - Subpastas" "Procurar também '$pad' nas subpastas de:\n\n$cam ?" 11 78 && rec="S"

    acao=$(ui --title "Adicionar (4/6) - Ação" --radiolist \
"O que fazer quando o limite de tamanho ou de tempo for atingido?" 14 78 3 \
        rodar    "Guardar cópia .gz e esvaziar   (ex.: alert.log)"        ON \
        esvaziar "Esvaziar sem cópia (truncate)  (ex.: alert.log)"        OFF \
        apagar   "Apagar os ficheiros            (ex.: *.trc, *.aud)"     OFF) || return

    [[ $acao == apagar ]] && def_sz="0" || def_sz="1G"
    while true; do
        maxsz=$(ui --title "Adicionar (5/6) - Tamanho máximo" --inputbox \
"Tamanho a partir do qual o ficheiro é $( [[ $acao == apagar ]] && echo apagado || echo limpo ).

Exemplos: 1G, 500M, 100K     (0 = sem limite de tamanho)" 12 78 "$def_sz") || return
        maxsz="${maxsz^^}"; maxsz="${maxsz// /}"
        (( $(para_bytes "$maxsz") >= 0 )) && break
        msg "Valor inválido" "Use um número seguido de K, M, G ou T (ex.: 1G), ou 0."
    done

    if [[ $acao == apagar ]]; then
        def_d=30; txt_d="Apagar ficheiros que não são modificados há mais de N dias."
    else
        def_d=0;  txt_d="Limpar o ficheiro a cada N dias, mesmo que não atinja o tamanho.
(a contagem começa na primeira execução)"
    fi
    while true; do
        maxd=$(ui --title "Adicionar (6/6) - Tempo" --inputbox \
"$txt_d

Número de dias     (0 = sem limite de tempo)" 13 78 "$def_d") || return
        [[ $maxd =~ ^[0-9]+$ ]] && break
        msg "Valor inválido" "Indique um número inteiro de dias."
    done

    if [[ $maxsz == 0 && $maxd == 0 ]]; then
        msg "Sem limites" "Tem de indicar pelo menos um limite (tamanho ou tempo).\nA entrada não foi adicionada."
        return
    fi

    if [[ $acao == rodar ]]; then
        while true; do
            dcop=$(ui --title "Cópias comprimidas" --inputbox \
"Durante quantos dias manter as cópias .gz?
(ex.: alert_ORCL.log.20261110_023000.gz)

0 = nunca apagar as cópias" 13 78 "30") || return
            [[ $dcop =~ ^[0-9]+$ ]] && break
            msg "Valor inválido" "Indique um número inteiro de dias."
        done
    fi

    if [[ $acao == apagar && $pad == "*" ]]; then
        sn_nao "Atenção" "Vai apagar TODOS os ficheiros de:\n\n$cam$( [[ $rec == S ]] && echo ' (e subpastas)')\n\nque cumpram o limite. Tem a certeza?" || return
    fi

    sn "Confirmar nova entrada" \
"Pasta     : $cam
Ficheiros : $pad   (subpastas: $([[ $rec == S ]] && echo sim || echo não))
Ação      : $(descr_acao "$acao")
Limites   : $(descr_limites "$acao" "$maxsz" "$maxd" "$dcop")

Guardar?" 15 90 || return

    ENTRADAS+=("$cam|$pad|$rec|$acao|$maxsz|$maxd|$dcop|S")
    guardar_conf
    msg "Guardado" "Entrada adicionada a:\n$CONF_FILE\n\nUse 'Simular limpeza' para ver o que seria feito."
}

# ---------------------------------------------------------------------
escolher_entrada() { # titulo -> índice (0-based) no stdout
    local args=() i cam pad rec acao maxsz maxd dcop ativo lin
    if (( ${#ENTRADAS[@]} == 0 )); then msg "$1" "Não existem entradas configuradas."; return 1; fi
    for i in "${!ENTRADAS[@]}"; do
        IFS='|' read -r cam pad rec acao maxsz maxd dcop ativo <<< "${ENTRADAS[$i]}"
        lin="$([[ $ativo == S ]] && echo ATIVO || echo INATIVO)  $acao  $cam/$pad"
        (( ${#lin} > W - 20 )) && lin="${lin:0:$(( W - 23 ))}..."
        args+=("$(( i + 1 ))" "$lin")
    done
    i=$(ui --title "$1" --menu "Escolha a entrada:" "$H" "$W" $(( H - 8 )) "${args[@]}") || return 1
    echo $(( i - 1 ))
}

alternar() {
    local i e ativo
    i=$(escolher_entrada "Ativar / desativar") || return
    e="${ENTRADAS[$i]}"; ativo="${e##*|}"
    [[ $ativo == S ]] && ativo=N || ativo=S
    ENTRADAS[$i]="${e%|*}|$ativo"
    guardar_conf
    msg "Atualizado" "Entrada $(( i + 1 )) está agora $([[ $ativo == S ]] && echo ATIVA || echo INATIVA)."
}

remover() {
    local i novo=() j
    i=$(escolher_entrada "Remover entrada") || return
    sn_nao "Remover" "Remover a entrada $(( i + 1 ))?\n\n${ENTRADAS[$i]//|/  |  }" 12 90 || return
    for j in "${!ENTRADAS[@]}"; do (( j == i )) || novo+=("${ENTRADAS[$j]}"); done
    ENTRADAS=("${novo[@]}")
    guardar_conf
    msg "Removido" "Entrada removida."
}

# ---------------------------------------------------------------------
simular_ui() {
    local tmp; tmp=$(mktemp)
    executar_limpeza SIMULACAO > "$tmp" 2>&1
    texto "Simulação (nada foi alterado)" "$tmp"
    rm -f "$tmp"
}

executar_ui() {
    sn_nao "Executar limpeza" "Executar agora a limpeza de todas as entradas ativas?\n\nSugestão: faça primeiro uma simulação." || return
    local tmp; tmp=$(mktemp)
    executar_limpeza EXEC > "$tmp" 2>&1
    texto "Resultado da limpeza" "$tmp"
    rm -f "$tmp"
}

# ---------------------------------------------------------------------
historico_ui() {
    local op tmp corte termo
    while true; do
        op=$(ui --title "Histórico" --menu "Histórico guardado em $HIST_DIR ($HIST_DIAS dias)" 16 74 6 \
            1 "Últimos 7 dias" \
            2 "Últimos 30 dias" \
            3 "Todo o histórico ($HIST_DIAS dias)" \
            4 "Resumo mensal (ações e espaço libertado)" \
            5 "Procurar (ficheiro, ação, data...)" \
            0 "Voltar") || return
        [[ $op == 0 ]] && return
        if [[ ! -d $HIST_DIR ]] || ! ls "$HIST_DIR"/????-??-??.log >/dev/null 2>&1; then
            msg "Histórico" "Ainda não existe histórico (nenhuma limpeza foi executada)."
            continue
        fi
        tmp=$(mktemp)
        case $op in
            1|2|3)
                case $op in 1) corte=$(date -d '-7 days' +%F) ;; 2) corte=$(date -d '-30 days' +%F) ;; 3) corte="0000-00-00" ;; esac
                historico_listar "$corte" "" > "$tmp" ;;
            4)  historico_resumo > "$tmp" ;;
            5)  termo=$(ui --title "Procurar" --inputbox "Texto a procurar (ex.: alert_ORCL, APAGAR, ERRO, 2026-11):" 10 74) || { rm -f "$tmp"; continue; }
                historico_listar "0000-00-00" "$termo" > "$tmp" ;;
        esac
        texto "Histórico" "$tmp"
        rm -f "$tmp"
    done
}

historico_listar() { # data_corte termo
    local f
    for f in $(ls "$HIST_DIR"/????-??-??.log | sort -r); do
        [[ "$(basename "$f" .log)" < "$1" ]] && continue
        tac "$f"
    done | awk -F'|' -v t="$2" '
        function h(b,  u,i){split("B K M G T",u," ");i=1;while(b>=1024&&i<5){b/=1024;i++}
            return (i==1)?sprintf("%dB",b):sprintf("%.1f%s",b,u[i])}
        t!="" && index($0,t)==0 {next}
        { n++
          if ($3=="RESUMO") { printf "%s  ==== EXECUÇÃO: %s, %s libertados ====\n\n", $1, $6, h($5); next }
          printf "%s  %-9s %8s  %s\n", $1, $3, h($5), $4
          printf "%21s%s -> %s\n\n", "", $6, $7 }
        END { if (n==0) print "Sem registos." }'
}

historico_resumo() {
    cat "$HIST_DIR"/????-??-??.log | awk -F'|' '
        function h(b,  u,i){split("B K M G T",u," ");i=1;while(b>=1024&&i<5){b/=1024;i++}
            return (i==1)?sprintf("%dB",b):sprintf("%.1f%s",b,u[i])}
        $3=="RESUMO" { m=substr($1,1,7); ex[m]++; next }
        $3=="AVISO"  { next }
        { m=substr($1,1,7); meses[m]=1
          if ($7 ~ /^ERRO/) { er[m]++; next }
          c[m,$3]++; b[m]+=$5; tb+=$5 }
        END {
          for (m in ex) meses[m]=1
          for (m in meses)
            printf "%-8s %6d %6d %9d %7d %10d %6d %11s\n", m, ex[m], c[m,"RODAR"], c[m,"ESVAZIAR"], c[m,"APAGAR"], c[m,"APAGAR_GZ"], er[m], h(b[m])
          printf "TOTAL|%s\n", h(tb) }' | {
        # (largura +1 em "Mês" e "Cópias gz" por causa dos acentos)
        printf "%-9s %6s %6s %9s %7s %11s %6s %11s\n" "Mês" "Exec." "Rodar" "Esvaziar" "Apagar" "Cópias gz" "Erros" "Libertado"
        printf "%-8s %6s %6s %9s %7s %10s %6s %11s\n" "--------" "-----" "-----" "--------" "------" "---------" "-----" "---------"
        local tot
        tot=$(cat)
        grep -v '^TOTAL|' <<< "$tot" | sort -r
        printf "\nTotal libertado no período: %s\n" "$(grep '^TOTAL|' <<< "$tot" | cut -d'|' -f2)"
    }
}

# ---------------------------------------------------------------------
gerar_cron() {
    local hora h m linha pre=""
    while true; do
        hora=$(ui --title "Crontab" --inputbox "Hora da execução diária (HH:MM):" 10 60 "02:30") || return
        [[ $hora =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]] && break
        msg "Hora inválida" "Use o formato HH:MM (ex.: 02:30)."
    done
    h=$((10#${BASH_REMATCH[1]})); m=$((10#${BASH_REMATCH[2]}))
    [[ -n ${GESTAO_LOGS_HOME:-} ]] && pre="GESTAO_LOGS_HOME=$BASE_DIR "
    linha="$m $h * * * ${pre}$SCRIPT_PATH --executar > $ULTIMA_EXEC 2>&1  # gestao_logs"
    echo "$linha" > "$CRON_FILE"

    if sn "Crontab" \
"Linha gerada (guardada em $CRON_FILE):

$linha

Deve correr como o dono dos ficheiros (ex.: oracle) ou como root.

Instalar já no crontab do utilizador '$(id -un)'?
(substitui uma linha anterior do gestao_logs, se existir)" 18 "$W"; then
        if ! command -v crontab >/dev/null 2>&1; then msg "Crontab" "O comando 'crontab' não está disponível."; return; fi
        if { crontab -l 2>/dev/null | grep -v '# gestao_logs$'; echo "$linha"; } | crontab -; then
            msg "Crontab" "Instalado. Verifique com:  crontab -l"
        else
            msg "Crontab" "ERRO ao instalar no crontab."
        fi
    fi
}

gerar_logrotate() {
    local e cam pad rec acao maxsz maxd dcop ativo dono grupo intervalo n=0
    if (( ${#ENTRADAS[@]} == 0 )); then msg "Logrotate" "Não existem entradas configuradas."; return; fi
    {
        echo "# ===================================================================="
        echo "# Gerado por gestao_logs.sh em $(date '+%Y-%m-%d %H:%M:%S')"
        echo "# Copiar para /etc/logrotate.d/gestao_logs (como root)"
        echo "# Testar: logrotate -d /etc/logrotate.d/gestao_logs"
        echo "# NOTA: não use o cron do gestao_logs e o logrotate para as mesmas entradas."
        echo "# NOTA: o logrotate só suporta intervalos diário/semanal/mensal/anual."
        echo "# ===================================================================="
        for e in "${ENTRADAS[@]}"; do
            IFS='|' read -r cam pad rec acao maxsz maxd dcop ativo <<< "$e"
            echo
            if [[ $ativo != S ]]; then echo "# [inativa] $cam/$pad"; continue; fi
            if [[ $acao == apagar ]]; then echo "# [não suportado - apagar] $cam/$pad -> usar modo cron (--executar)"; continue; fi
            if [[ $rec == S ]]; then echo "# [não suportado - subpastas] $cam/$pad -> usar modo cron (--executar)"; continue; fi
            dono=$(stat -c %U "$cam" 2>/dev/null || echo oracle)
            grupo=$(stat -c %G "$cam" 2>/dev/null || echo oinstall)
            if   (( maxd == 0 )); then intervalo=""
            elif (( maxd <= 1 )); then intervalo="daily"
            elif (( maxd <= 7 )); then intervalo="weekly"
            elif (( maxd <= 31 )); then intervalo="monthly"
            else intervalo="yearly"; fi
            echo "\"$cam/$pad\" {"
            echo "    su $dono $grupo"
            if [[ $maxsz != 0 && -n $intervalo ]]; then
                echo "    $intervalo"; echo "    maxsize ${maxsz,,}"
            elif [[ $maxsz != 0 ]]; then
                echo "    size ${maxsz,,}"
            else
                echo "    $intervalo"
            fi
            echo "    copytruncate"
            echo "    missingok"
            echo "    notifempty"
            if [[ $acao == rodar ]]; then
                echo "    rotate 9999"
                (( dcop > 0 )) && echo "    maxage $dcop"
                echo "    compress"
                echo "    dateext"
                echo "    dateformat .%Y%m%d_%s"
            else
                echo "    rotate 0"
            fi
            echo "}"
            n=$(( n + 1 ))
        done
    } > "$LOGROTATE_FILE"

    texto "Logrotate gerado: $LOGROTATE_FILE ($n bloco(s))" "$LOGROTATE_FILE"

    if [[ $(id -u) -eq 0 ]]; then
        if sn_nao "Logrotate" "Copiar para /etc/logrotate.d/gestao_logs ?"; then
            cp -f "$LOGROTATE_FILE" /etc/logrotate.d/gestao_logs && chmod 644 /etc/logrotate.d/gestao_logs &&
                msg "Logrotate" "Copiado. Teste com:\n\nlogrotate -d /etc/logrotate.d/gestao_logs"
        fi
    else
        msg "Logrotate" "Para instalar (como root):\n\ncp $LOGROTATE_FILE /etc/logrotate.d/gestao_logs\nlogrotate -d /etc/logrotate.d/gestao_logs" 13 "$W"
    fi
}

definicoes() {
    local d
    while true; do
        d=$(ui --title "Definições" --inputbox "Dias de histórico a manter:" 10 60 "$HIST_DIAS") || return
        [[ $d =~ ^[0-9]+$ ]] && (( d > 0 )) && break
        msg "Valor inválido" "Indique um número inteiro maior que 0."
    done
    HIST_DIAS=$d; guardar_conf
    msg "Definições" "Histórico: $HIST_DIAS dias."
}

menu_principal() {
    local op
    while true; do
        carregar_conf
        op=$(ui --title "Menu principal" --menu \
"Entradas configuradas: ${#ENTRADAS[@]}     Histórico: $HIST_DIAS dias
Configuração: $CONF_FILE" 22 76 12 \
            1  "Ver configuração atual" \
            2  "Adicionar caminho / ficheiro" \
            3  "Ativar / desativar entrada" \
            4  "Remover entrada" \
            5  "Simular limpeza (não altera nada)" \
            6  "Executar limpeza agora" \
            7  "Ver histórico" \
            8  "Gerar / instalar crontab" \
            9  "Gerar configuração logrotate" \
            10 "Definições" \
            0  "Sair") || break
        case $op in
            1) ver_conf ;;
            2) adicionar ;;
            3) alternar ;;
            4) remover ;;
            5) simular_ui ;;
            6) executar_ui ;;
            7) historico_ui ;;
            8) gerar_cron ;;
            9) gerar_logrotate ;;
            10) definicoes ;;
            0) break ;;
        esac
    done
    clear
}

ajuda() {
    sed -n '2,24p' "$SCRIPT_PATH" | sed 's/^# \{0,1\}//'
}

# =====================================================================
#  Main
# =====================================================================
mkdir -p "$BASE_DIR" 2>/dev/null
case "${1:-}" in
    --executar) executar_limpeza EXEC; exit $? ;;
    --simular)  executar_limpeza SIMULACAO; exit $? ;;
    --ajuda|-h|--help) ajuda; exit 0 ;;
    "")         detetar_ui; carregar_conf; boas_vindas; menu_principal ;;
    *)          echo "Opção desconhecida: $1   (use --ajuda)"; exit 1 ;;
esac
