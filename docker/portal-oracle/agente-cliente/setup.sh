#!/usr/bin/env bash
# Instalacao do agente cliente do portal em /opt/agente-portal (Debian/Ubuntu e RHEL/Rocky/Alma/Oracle Linux).
#
#   sudo bash setup.sh --portal https://<portal> --id <ID> --token <TOKEN> [--utilizador <utilizador_ssh>]
#        [--intervalo 60] [--grupo "oracle postgres"] [--inseguro] [--ca /caminho/ca.pem] [--sem-ssh]
#   sudo bash setup.sh --verificar --utilizador <utilizador_ssh> [--portal URL]  # verifica e corrige o SSH
#   sudo bash setup.sh --desinstalar
#
# Faz: verifica o utilizador SSH (existe, ~/.ssh 700, authorized_keys 600, dono certo) e acrescenta a chave
# publica do portal; instala o python3/curl se faltarem; copia o agente; grava o .env com o token cifrado;
# cria o grupo agente-portal (quem pode notificar pelo socket) e o servico systemd.
set -euo pipefail
DIR=/opt/agente-portal; SVC=agente-portal; GRUPO=agente-portal
PORTAL=""; ID=""; TOKEN=""; UTIL=""; INTERVALO=60; GRUPO_UTIL=""; INSEGURO=0; CA=""; SEM_SSH=0; MODO=instalar
V=$'\e[32m'; A=$'\e[33m'; E=$'\e[31m'; N=$'\e[0m'
ok()   { echo "  ${V}OK${N}       $*"; }
corr() { echo "  ${A}CORRIGIDO${N} $*"; }
avs()  { echo "  ${A}AVISO${N}    $*"; }
erro() { echo "  ${E}ERRO${N}     $*" >&2; exit 1; }
tit()  { echo; echo "== $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --portal) PORTAL=${2%/}; shift ;;
        --id) ID=$2; shift ;;
        --token) TOKEN=$2; shift ;;
        --utilizador) UTIL=$2; shift ;;
        --intervalo) INTERVALO=$2; shift ;;
        --grupo) GRUPO_UTIL=$2; shift ;;
        --ca) CA=$2; shift ;;
        --inseguro) INSEGURO=1 ;;
        --sem-ssh) SEM_SSH=1 ;;
        --verificar) MODO=verificar ;;
        --desinstalar) MODO=desinstalar ;;
        -h|--help) sed -n 2,13p "$0"; exit 0 ;;
        *) erro "opcao desconhecida: $1" ;;
    esac
    shift
done
[[ $EUID -eq 0 ]] || erro "corra como root (sudo)"
K=(); [[ $INSEGURO == 1 ]] && K=(-k)

if [[ $MODO == desinstalar ]]; then
    systemctl disable --now $SVC 2>/dev/null || true
    rm -f /etc/systemd/system/$SVC.service /usr/local/bin/agente-portal; systemctl daemon-reload
    rm -rf "$DIR" /run/agente-portal
    echo "Agente removido. (O grupo $GRUPO e a chave do portal em authorized_keys ficam; retire-os a mao se quiser.)"
    exit 0
fi

# ---------------------------------------------------------------- distribuicao
tit "Sistema"
. /etc/os-release 2>/dev/null || true
FAM=other
[[ " ${ID_LIKE:-} ${ID:-} " =~ (debian|ubuntu) ]] && FAM=debian
[[ " ${ID_LIKE:-} ${ID:-} " =~ (rhel|fedora|centos) ]] && FAM=redhat
ok "${PRETTY_NAME:-desconhecido} (familia: $FAM)"
VIRT=$(systemd-detect-virt 2>/dev/null || echo none); ok "virtualizacao: $VIRT"

instalar_pacotes() {
    local falta=()
    command -v python3 >/dev/null || falta+=(python3)
    command -v curl >/dev/null || falta+=(curl)
    [[ ${#falta[@]} -eq 0 ]] && { ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2) e curl presentes"; return; }
    case $FAM in
        debian) apt-get update -qq && apt-get install -y -qq "${falta[@]}" ;;
        redhat) (command -v dnf >/dev/null && dnf install -y -q "${falta[@]}") || yum install -y -q "${falta[@]}" ;;
        *) erro "instale: ${falta[*]}" ;;
    esac
    corr "instalado: ${falta[*]}"
}

# ---------------------------------------------------------------- utilizador SSH
verificar_ssh() {
    tit "Utilizador SSH do portal${UTIL:+ ($UTIL)}"
    [[ -n $UTIL ]] || { avs "sem --utilizador: verificacoes de SSH ignoradas"; return; }
    id "$UTIL" >/dev/null 2>&1 || erro "o utilizador '$UTIL' nao existe (crie-o ou use o do Ansible)"
    ok "existe: $(id "$UTIL")"
    local home grp_ s
    home=$(getent passwd "$UTIL" | cut -d: -f6); grp_=$(id -gn "$UTIL")
    [[ -d $home ]] || erro "a home $home nao existe"
    s=$(getent passwd "$UTIL" | cut -d: -f7)
    [[ $s =~ (nologin|false)$ ]] && avs "shell $s: o SSH com comandos nao funciona" || ok "shell $s"
    [[ $(stat -c %a "$home") =~ ^7[0-5][0-5]$ ]] && ok "home $home ($(stat -c %a "$home"))" || { chmod go-w "$home"; corr "home sem escrita para grupo/outros"; }
    if [[ ! -d $home/.ssh ]]; then install -d -m 700 -o "$UTIL" -g "$grp_" "$home/.ssh"; corr "criado $home/.ssh (700)"; fi
    [[ $(stat -c %a "$home/.ssh") == 700 ]] && ok "$home/.ssh 700" || { chmod 700 "$home/.ssh"; corr "$home/.ssh -> 700"; }
    [[ $(stat -c %U "$home/.ssh") == "$UTIL" ]] && ok "dono de .ssh: $UTIL" || { chown "$UTIL:$grp_" "$home/.ssh"; corr "dono de .ssh -> $UTIL"; }
    local ak=$home/.ssh/authorized_keys
    if [[ ! -f $ak ]]; then install -m 600 -o "$UTIL" -g "$grp_" /dev/null "$ak"; corr "criado $ak (600)"; fi
    [[ $(stat -c %a "$ak") == 600 ]] && ok "authorized_keys 600" || { chmod 600 "$ak"; corr "authorized_keys -> 600"; }
    [[ $(stat -c %U "$ak") == "$UTIL" ]] && ok "dono de authorized_keys: $UTIL" || { chown "$UTIL:$grp_" "$ak"; corr "dono de authorized_keys -> $UTIL"; }
    ok "$(grep -c '^[^#]' "$ak" || true) chave(s) em authorized_keys"
    if [[ $SEM_SSH -eq 0 && -n $PORTAL ]]; then
        local chave
        chave=$(curl -fsS ${K[@]+"${K[@]}"} "$PORTAL/clientes/agente/chave.pub") || erro "nao consegui obter a chave publica do portal ($PORTAL/clientes/agente/chave.pub)"
        [[ $chave =~ ^ssh-(ed25519|rsa)\  ]] || erro "chave publica do portal invalida"
        if grep -qF "$(echo "$chave" | awk '{print $2}')" "$ak"; then ok "a chave publica do portal ja esta em authorized_keys"
        else echo "$chave" >> "$ak"; corr "chave publica do portal acrescentada ($(echo "$chave" | awk '{print $3}'))"; fi
    fi
    if command -v getenforce >/dev/null && [[ $(getenforce) == Enforcing ]] && command -v restorecon >/dev/null; then
        restorecon -R "$home/.ssh" && ok "contexto SELinux de .ssh reposto"
    fi
    if command -v sshd >/dev/null; then
        local cfg; cfg=$(sshd -T 2>/dev/null || true)
        grep -qi '^pubkeyauthentication yes' <<<"$cfg" && ok "sshd aceita chaves" || avs "sshd: PubkeyAuthentication pode estar desligado"
        if grep -qi '^allowusers' <<<"$cfg" && ! grep -qiE "^allowusers.*\b$UTIL\b" <<<"$cfg"; then avs "sshd AllowUsers nao inclui $UTIL"; fi
    fi
}

instalar_pacotes
verificar_ssh
[[ $MODO == verificar ]] && { echo; echo "Verificacao concluida."; exit 0; }

# ---------------------------------------------------------------- agente
tit "Agente ($DIR)"
[[ -n $PORTAL && -n $ID && -n $TOKEN ]] || erro "faltam --portal, --id e --token (ver no portal: Configuracao > Clientes)"
[[ $INTERVALO =~ ^[0-9]+$ && $INTERVALO -ge 10 && $INTERVALO -le 3600 ]] || erro "--intervalo entre 10 e 3600"
umask 022
install -d -m 755 "$DIR"
curl -fsS ${K[@]+"${K[@]}"} "$PORTAL/clientes/agente/agente-portal.py" -o "$DIR/agente-portal.py.novo" || erro "download do agente falhou"
python3 -m py_compile "$DIR/agente-portal.py.novo" 2>/dev/null || python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$DIR/agente-portal.py.novo" || erro "agente descarregado invalido"
mv "$DIR/agente-portal.py.novo" "$DIR/agente-portal.py"; chmod 755 "$DIR/agente-portal.py"; rm -rf "$DIR/__pycache__"
ln -sf "$DIR/agente-portal.py" /usr/local/bin/agente-portal
ok "agente $("$DIR/agente-portal.py" versao) em $DIR"
ARGS=(--portal "$PORTAL" --id "$ID" --token "$TOKEN" --intervalo "$INTERVALO")
[[ -n $UTIL ]] && ARGS+=(--utilizador "$UTIL"); [[ -n $CA ]] && ARGS+=(--ca "$CA"); [[ $INSEGURO == 1 ]] && ARGS+=(--inseguro)
"$DIR/agente-portal.py" configurar "${ARGS[@]}" >/dev/null
chmod 600 "$DIR/.env"; ok ".env 600 com o token cifrado (chave $DIR/.chave 400, ligada a /etc/machine-id)"

getent group $GRUPO >/dev/null || { groupadd -r $GRUPO; corr "grupo $GRUPO criado"; }
for u in $GRUPO_UTIL; do
    if id "$u" >/dev/null 2>&1; then usermod -aG $GRUPO "$u"; ok "$u no grupo $GRUPO (pode usar 'agente-portal notificar')"; else avs "utilizador $u nao existe"; fi
done

cat > /etc/systemd/system/$SVC.service <<UNIT
[Unit]
Description=Agente cliente do portal
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $DIR/agente-portal.py run
Restart=always
RestartSec=10
RuntimeDirectory=agente-portal
RuntimeDirectoryMode=0750
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q $SVC
systemctl restart $SVC
sleep 2
systemctl is-active -q $SVC && ok "servico $SVC ativo" || erro "o servico nao arrancou: journalctl -u $SVC -n 30"

tit "Teste"
"$DIR/agente-portal.py" testar || avs "o teste falhou (veja acima); o servico tenta de novo a cada $INTERVALO s"
cat <<FIM

Concluido. Comandos uteis:
  agente-portal estado                       # estado do agente
  agente-portal intervalo 120                # mudar o intervalo de sincronizacao
  agente-portal notificar <ID> ok "texto"    # notificar o portal (ID da regra no portal)
  journalctl -u $SVC -f                      # registo do servico
FIM
