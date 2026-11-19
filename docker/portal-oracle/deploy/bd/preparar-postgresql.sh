#!/usr/bin/env bash
#
# preparar-postgresql.sh - prepara um servidor PostgreSQL para a base de dados do portal.
# Corre NO SERVIDOR PostgreSQL, como root. Nao depende de mais nenhum ficheiro do portal.
#
#   sudo ./preparar-postgresql.sh --bd portal_oracle --utilizador portal_oracle --cliente 10.0.0.5
#
# Faz:
#   1. cria (ou atualiza a password de) o utilizador e cria a base de dados;
#   2. acrescenta ao pg_hba.conf a linha que permite a ligacao a partir do --cliente;
#   3. se o PostgreSQL so escutar em localhost, passa a escutar na rede (pede confirmacao: reinicia);
#   4. recarrega a configuracao;
#   5. se houver ufw/firewalld ativo, abre a porta so para o --cliente (pede confirmacao).
#
# A password e lida da variavel PORTAL_DB_PASSWORD; se nao existir, e pedida.
#
set -Eeuo pipefail

BD=""; UTIL=""; CLIENTES=""; PORTA=5432
SEM_REDE=0; SEM_FIREWALL=0; SIM=0

uso() {
    sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'
Opcoes:
  --bd NOME            base de dados (obrigatorio)
  --utilizador NOME    utilizador do portal (obrigatorio)
  --cliente IP[,IP]    IP(s) do servidor do portal (obrigatorio); 127.0.0.1 = local
  --porta N            porta do PostgreSQL (so para a firewall; default 5432)
  --sem-rede           nao alterar listen_addresses
  --sem-firewall       nao mexer na firewall
  -y, --sim            responder "sim" as confirmacoes
EOF
}

while (( $# )); do
    case "$1" in
        --bd) BD=$2; shift ;;
        --utilizador) UTIL=$2; shift ;;
        --cliente) CLIENTES=$2; shift ;;
        --porta) PORTA=$2; shift ;;
        --sem-rede) SEM_REDE=1 ;;
        --sem-firewall) SEM_FIREWALL=1 ;;
        -y|--sim) SIM=1 ;;
        -h|--ajuda|--help) uso; exit 0 ;;
        *) echo "Opcao desconhecida: $1" >&2; uso; exit 2 ;;
    esac
    shift
done

erro() { echo "ERRO: $*" >&2; exit 1; }
info() { echo "  - $*"; }
confirmar() {
    (( SIM )) && return 0
    local r; read -r -p "  $1 [s/N] " r </dev/tty || true
    [[ ${r,,} == s* ]]
}

[[ $EUID -eq 0 ]] || erro "corra como root (sudo)."
[[ $BD =~ ^[A-Za-z_][A-Za-z0-9_-]{0,62}$ ]] || erro "--bd invalido ou em falta."
[[ $UTIL =~ ^[A-Za-z_][A-Za-z0-9_-]{0,62}$ ]] || erro "--utilizador invalido ou em falta."
[[ -n $CLIENTES ]] || erro "--cliente em falta."
command -v psql >/dev/null || erro "psql nao encontrado: este servidor tem o PostgreSQL instalado?"

PW=${PORTAL_DB_PASSWORD:-}
if [[ -z $PW ]]; then
    read -r -s -p "Password para o utilizador $UTIL: " PW </dev/tty; echo
    [[ -n $PW ]] || erro "password vazia."
fi

pg() { runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 "$@"; }
pgval() { runuser -u postgres -- psql -X -At -v ON_ERROR_STOP=1 -c "$1"; }

echo "== PostgreSQL: utilizador e base de dados"
pg -v u="$UTIL" -v pw="$PW" -v bd="$BD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'u')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'u', :'pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0', :'bd', :'u', 'UTF8')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'bd') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'bd', :'u') \gexec
SQL
info "utilizador $UTIL e base de dados $BD prontos"

echo "== pg_hba.conf"
HBA=$(pgval "SHOW hba_file")
METODO=$(pgval "SHOW password_encryption")
[[ $METODO == scram-sha-256 ]] || METODO=md5
[[ -f $HBA ]] || erro "pg_hba.conf nao encontrado: $HBA"
ALTEROU_HBA=0
IFS=',' read -r -a LISTA <<<"$CLIENTES"
for c in "${LISTA[@]}"; do
    c=${c// /}
    [[ $c == */* ]] || { [[ $c == *:* ]] && c="$c/128" || c="$c/32"; }
    LINHA="host    $BD    $UTIL    $c    $METODO"
    if grep -Eq "^[[:space:]]*host[[:space:]]+$BD[[:space:]]+$UTIL[[:space:]]+${c//./\\.}[[:space:]]" "$HBA"; then
        info "ja existe: $LINHA"
        continue
    fi
    (( ALTEROU_HBA )) || cp -p "$HBA" "$HBA.$(date +%Y%m%d%H%M%S).bak"
    # Inserir antes da primeira regra (a primeira linha que coincide e a que vale)
    awk -v l="$LINHA    # portal" 'done==0 && $0 !~ /^[[:space:]]*(#|$)/ {print l; done=1} {print} END{if(!done){print l}}' \
        "$HBA" > "$HBA.novo" && cat "$HBA.novo" > "$HBA" && rm -f "$HBA.novo"
    info "acrescentado: $LINHA"
    ALTEROU_HBA=1
done
(( ALTEROU_HBA )) && info "copia de seguranca em $HBA.<data>.bak"

REINICIAR=0
if (( ! SEM_REDE )) && [[ $CLIENTES != "127.0.0.1" && $CLIENTES != "::1" ]]; then
    echo "== listen_addresses"
    LISTEN=$(pgval "SHOW listen_addresses")
    if [[ $LISTEN == "localhost" || $LISTEN == "127.0.0.1" || -z $LISTEN ]]; then
        echo "  O PostgreSQL so escuta em '$LISTEN'. Para aceitar ligacoes remotas passa a escutar em '*'."
        if confirmar "Alterar (ALTER SYSTEM) e reiniciar o PostgreSQL?"; then
            pg -c "ALTER SYSTEM SET listen_addresses = '*'"
            REINICIAR=1
        else
            info "nao alterado: o portal nao vai conseguir ligar enquanto nao o fizer"
        fi
    else
        info "ja escuta em '$LISTEN'"
    fi
fi

echo "== Aplicar"
if (( REINICIAR )); then
    UNIDADE=$(systemctl list-units --type=service --state=running --no-legend 'postgresql*' 2>/dev/null \
              | awk '{print $1}' | grep -v '^postgresql.service$' | head -1)
    systemctl restart "${UNIDADE:-postgresql}" || systemctl restart postgresql
    info "PostgreSQL reiniciado"
else
    pg -c "SELECT pg_reload_conf()" >/dev/null
    info "configuracao recarregada"
fi

if (( ! SEM_FIREWALL )); then
    for c in "${LISTA[@]}"; do
        c=${c// /}
        [[ $c == 127.0.0.1 || $c == ::1 ]] && continue
        if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
            confirmar "ufw ativo: permitir $c na porta $PORTA/tcp?" && ufw allow from "$c" to any port "$PORTA" proto tcp
        elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
            if confirmar "firewalld ativo: permitir $c na porta $PORTA/tcp?"; then
                firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=$c port port=$PORTA protocol=tcp accept"
                firewall-cmd --reload
            fi
        fi
    done
fi

echo "== Concluido: base de dados $BD, utilizador $UTIL, acesso a partir de $CLIENTES"
