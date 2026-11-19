#!/usr/bin/env bash
#
# preparar-mariadb.sh - prepara um servidor MariaDB / MySQL para a base de dados do portal.
# Corre NO SERVIDOR MariaDB, como root. Nao depende de mais nenhum ficheiro do portal.
#
#   sudo ./preparar-mariadb.sh --bd portal_oracle --utilizador portal_oracle --cliente 10.0.0.5
#
# Faz:
#   1. cria a base de dados (utf8mb4) e o utilizador '<utilizador>'@'<cliente>' com as permissoes
#      necessarias (se ja existir, atualiza a password);
#   2. se o MariaDB so escutar em 127.0.0.1, passa a escutar na rede (pede confirmacao: reinicia);
#   3. se houver ufw/firewalld ativo, abre a porta so para o --cliente (pede confirmacao).
#
# A password e lida da variavel PORTAL_DB_PASSWORD; se nao existir, e pedida.
# Liga como root pelo socket (unix_socket). Se o root tiver password, defina MYSQL_PWD.
#
set -Eeuo pipefail

BD=""; UTIL=""; CLIENTES=""; PORTA=3306
SEM_REDE=0; SEM_FIREWALL=0; SIM=0

uso() {
    sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'
Opcoes:
  --bd NOME            base de dados (obrigatorio)
  --utilizador NOME    utilizador do portal (obrigatorio)
  --cliente HOST[,..]  IP(s) do servidor do portal (obrigatorio); localhost = local
                       aceita padroes do MariaDB, ex. 10.0.0.%
  --porta N            porta do MariaDB (so para a firewall; default 3306)
  --sem-rede           nao alterar bind-address
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
[[ $BD =~ ^[A-Za-z0-9_-]{1,64}$ ]] || erro "--bd invalido ou em falta."
[[ $UTIL =~ ^[A-Za-z0-9_-]{1,32}$ ]] || erro "--utilizador invalido ou em falta."
[[ $CLIENTES =~ ^[A-Za-z0-9.:%_,-]+$ ]] || erro "--cliente invalido ou em falta."

MYSQL=$(command -v mariadb || command -v mysql || true)
[[ -n $MYSQL ]] || erro "cliente mariadb/mysql nao encontrado: este servidor tem o MariaDB instalado?"
sql() { "$MYSQL" --protocol=socket -uroot -N -B "$@"; }
sql -e "SELECT 1" >/dev/null 2>&1 \
    || erro "nao foi possivel ligar como root pelo socket. Se o root tiver password: sudo MYSQL_PWD='...' $0 ..."

PW=${PORTAL_DB_PASSWORD:-}
if [[ -z $PW ]]; then
    read -r -s -p "Password para o utilizador $UTIL: " PW </dev/tty; echo
    [[ -n $PW ]] || erro "password vazia."
fi
esc() { local s=${1//\\/\\\\}; printf '%s' "${s//\'/\\\'}"; }   # escapar para '...'
PWE=$(esc "$PW")

echo "== MariaDB: base de dados e utilizador"
sql -e "CREATE DATABASE IF NOT EXISTS \`$BD\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
IFS=',' read -r -a LISTA <<<"$CLIENTES"
for c in "${LISTA[@]}"; do
    c=${c// /}
    sql <<SQL
CREATE USER IF NOT EXISTS '$UTIL'@'$c' IDENTIFIED BY '$PWE';
ALTER USER '$UTIL'@'$c' IDENTIFIED BY '$PWE';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP ON \`$BD\`.* TO '$UTIL'@'$c';
FLUSH PRIVILEGES;
SQL
    info "'$UTIL'@'$c' com acesso a \`$BD\`"
done

LOCAL=1
for c in "${LISTA[@]}"; do [[ $c == localhost || $c == 127.0.0.1 || $c == ::1 ]] || LOCAL=0; done

if (( ! SEM_REDE && ! LOCAL )); then
    echo "== bind-address"
    SKIP=$(sql -e "SELECT @@skip_networking" 2>/dev/null || echo 0)
    BIND=$(sql -e "SELECT @@bind_address" 2>/dev/null || echo "?")
    if [[ $SKIP == 1 || $BIND == 127.0.0.1 || $BIND == localhost || $BIND == ::1 ]]; then
        echo "  O MariaDB nao aceita ligacoes de rede (bind-address=$BIND, skip_networking=$SKIP)."
        DIRCNF=""
        for d in /etc/mysql/mariadb.conf.d /etc/mysql/mysql.conf.d /etc/my.cnf.d /etc/mysql/conf.d; do
            [[ -d $d ]] && { DIRCNF=$d; break; }
        done
        [[ -n $DIRCNF ]] || erro "pasta de configuracao do MariaDB nao encontrada; altere o bind-address a mao."
        if confirmar "Criar $DIRCNF/99-acesso-remoto.cnf (bind-address = 0.0.0.0) e reiniciar o MariaDB?"; then
            printf '# Acesso remoto (base de dados do portal)\n[mysqld]\nbind-address = 0.0.0.0\nskip-networking = 0\n' \
                > "$DIRCNF/99-acesso-remoto.cnf"
            systemctl restart mariadb 2>/dev/null || systemctl restart mysql 2>/dev/null || systemctl restart mysqld
            info "MariaDB reiniciado"
        else
            info "nao alterado: o portal nao vai conseguir ligar enquanto nao o fizer"
        fi
    else
        info "ja escuta em '$BIND'"
    fi
fi

if (( ! SEM_FIREWALL && ! LOCAL )); then
    for c in "${LISTA[@]}"; do
        c=${c// /}
        [[ $c == *%* ]] && { info "firewall: padrao $c, configure a mao"; continue; }
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
