#!/bin/bash
# Prepara SSH, diretorios e variaveis; depois arranca o supervisord (CMD).
set -e
DATA=/srv/portal/data
HOME_P=/home/portal

if [ ! -f /srv/portal/wsgi.py ]; then
    echo "ERRO: /srv/portal esta vazio. Confirme o volume './:/srv/portal' no docker-compose.yml."
    exit 1
fi

mkdir -p "$DATA/ssh" "$DATA/backups" /run/sshd

# Chaves SSH autorizadas ficam DENTRO do contentor (/etc/ssh/authorized_keys/<utilizador>, root 644):
# o sshd le-as com a identidade do utilizador, e em mounts de NAS (Synology, NFS, CIFS) as
# permissoes/ACL da partilha podem impedir essa leitura nas homes. Sao recriadas a cada arranque.
AK=/etc/ssh/authorized_keys
rm -rf "$AK"; install -d -m 755 -o root -g root "$AK"
chmod 755 /home 2>/dev/null || true

# --- pacotes Python: se o requirements.txt (no mount) mudou desde o build, instala as diferencas
REQ=/srv/portal/requirements.txt
if [ -f "$REQ" ] && [ "$(sha256sum "$REQ" | cut -d' ' -f1)" != "$(cat /opt/venv/.requirements.sha256 2>/dev/null)" ]; then
    echo "requirements.txt alterado: a instalar pacotes..."
    if /opt/venv/bin/pip install --no-cache-dir -q --timeout 15 --retries 1 -r "$REQ"; then
        sha256sum "$REQ" | cut -d' ' -f1 > /opt/venv/.requirements.sha256
    else
        echo "AVISO: pip install falhou (sem acesso ao PyPI?). O portal arranca com os pacotes da imagem."
    fi
fi

# --- chaves do servidor SSH persistentes (a impressao digital nao muda ao recriar o contentor)
[ -f "$DATA/ssh/ssh_host_ed25519_key" ] || ssh-keygen -q -t ed25519 -N '' -f "$DATA/ssh/ssh_host_ed25519_key"
[ -f "$DATA/ssh/ssh_host_rsa_key" ]     || ssh-keygen -q -t rsa -b 3072 -N '' -f "$DATA/ssh/ssh_host_rsa_key"
chown root:root "$DATA/ssh"/ssh_host_*; chmod 600 "$DATA/ssh"/ssh_host_*_key

# --- homes em data/home (/home). Versao anterior montava data/home diretamente em /home/portal.
if [ -e /home/.bashrc ] && [ ! -d /home/portal/.ssh ]; then
    mkdir -p /home/portal
    find /home -mindepth 1 -maxdepth 1 ! -name portal -exec mv -t /home/portal {} +
fi
mkdir -p "$HOME_P"
[ -f "$HOME_P/.bashrc" ] || cp -rT /etc/skel "$HOME_P"
chown portal:portal "$HOME_P"; chmod 755 "$HOME_P"
install -d -m 700 -o portal -g portal "$HOME_P/.ssh"; chmod 700 "$HOME_P/.ssh"
touch "$HOME_P/.ssh/authorized_keys"
if [ -f "$DATA/ssh/authorized_keys" ]; then
    cat "$DATA/ssh/authorized_keys" >> "$HOME_P/.ssh/authorized_keys"
fi
if [ -n "$SSH_AUTHORIZED_KEYS" ]; then
    printf '%s\n' "$SSH_AUTHORIZED_KEYS" >> "$HOME_P/.ssh/authorized_keys"
fi
tr -d '\r' < "$HOME_P/.ssh/authorized_keys" | grep -E '^(ssh-|ecdsa-|sk-)' | sort -u > /tmp/ak || true
mv /tmp/ak "$HOME_P/.ssh/authorized_keys"
install -m 644 -o root -g root "$HOME_P/.ssh/authorized_keys" "$AK/portal"
chown portal:portal "$HOME_P/.ssh/authorized_keys" 2>/dev/null; chmod 600 "$HOME_P/.ssh/authorized_keys" 2>/dev/null || true

# --- utilizadores SSH pessoais, com sudo: um ficheiro data/ssh/users/<utilizador>.pub cada
#     (pode ter varias chaves, uma por linha). O UID fica em <utilizador>.uid para nao mudar.
USERS_DIR="$DATA/ssh/users"
mkdir -p "$USERS_DIR"
SUDOERS=/etc/sudoers.d/ssh-users
: > "$SUDOERS"
for pub in "$USERS_DIR"/*.pub; do
    [ -e "$pub" ] || continue
    u=$(basename "$pub" .pub)
    if ! [[ "$u" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || [ "$u" = "portal" ] || [ "$u" = "root" ]; then
        echo "AVISO: nome de utilizador invalido ou reservado: '$u' (ignorado)"; continue
    fi
    if ! id "$u" >/dev/null 2>&1; then
        uidf="$USERS_DIR/$u.uid"
        if [ -s "$uidf" ]; then
            uid=$(cat "$uidf")
        else
            uid=1001
            while getent passwd "$uid" >/dev/null || cat "$USERS_DIR"/*.uid 2>/dev/null | grep -qx "$uid"; do
                uid=$((uid + 1))
            done
            echo "$uid" > "$uidf"
        fi
        useradd --uid "$uid" --home-dir "/home/$u" --no-create-home --shell /bin/bash "$u"
    fi
    usermod -aG portal,sshusers "$u"     # portal: editar o codigo e usar supervisorctl
    usermod -p '*' "$u"                   # sem password (so chave), conta nao bloqueada
    mkdir -p "/home/$u"
    [ -f "/home/$u/.bashrc" ] || cp -rT /etc/skel "/home/$u"
    # o sshd (StrictModes) recusa a chave se a home ou o .ssh tiverem escrita para grupo/outros
    chown "$u:$u" "/home/$u"; chmod 755 "/home/$u"
    install -d -m 700 -o "$u" -g "$u" "/home/$u/.ssh"; chmod 700 "/home/$u/.ssh"
    # chaves criadas no Windows: remover CR (\r) e linhas vazias
    tr -d '\r' < "$pub" | grep -E '^(ssh-|ecdsa-|sk-)' > "$AK/$u" || true
    chown root:root "$AK/$u"; chmod 644 "$AK/$u"
    n=$(wc -l < "$AK/$u")
    [ "$n" -gt 0 ] || echo "AVISO: $pub nao tem nenhuma chave no formato OpenSSH ('ssh-ed25519 AAAA...')"
    echo "$u ALL=(ALL) NOPASSWD:ALL" >> "$SUDOERS"
    echo "Utilizador SSH: $u (uid $(id -u "$u"), $n chave(s), sudo)"
done
chmod 440 "$SUDOERS"
visudo -c -f "$SUDOERS" >/dev/null 2>&1 || echo "AVISO: 'visudo -c' nao validou $SUDOERS - confirme com: sudo -l -U <utilizador>"

# --- password SSH (opcional; preferir chaves)
if [ -n "$SSH_PASSWORD" ]; then
    echo "portal:$SSH_PASSWORD" | chpasswd
    echo "PasswordAuthentication yes" > /etc/ssh/sshd_config.d/00-auth.conf
else
    echo "PasswordAuthentication no"  > /etc/ssh/sshd_config.d/00-auth.conf
fi

# --- sudo para o utilizador portal (opcional)
if [ "${PORTAL_SSH_SUDO:-0}" = "1" ]; then
    echo "portal ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/portal; chmod 440 /etc/sudoers.d/portal
else
    rm -f /etc/sudoers.d/portal
fi

# --- variaveis PORTAL_* / TZ visiveis nas sessoes SSH e no cron
env | grep -E '^(PORTAL_|TZ=)' | sed 's/^/export /; s/=\(.*\)$/="\1"/' > /etc/profile.d/portal.sh || true
echo "umask 002   # ficheiros criados ficam editaveis pelo grupo 'portal'" >> /etc/profile.d/portal.sh
echo 'export VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH"   # python/pip do portal' >> /etc/profile.d/portal.sh
ln -sf "/usr/share/zoneinfo/${TZ:-Europe/Lisbon}" /etc/localtime

# --- diretorio de dados do portal
chown portal:portal "$DATA" "$DATA/backups"
for f in portal.json admin.json fernet.key flask_secret.key portal.sqlite3; do
    [ -e "$DATA/$f" ] && chown portal:portal "$DATA/$f"
done

if [ ! -s "$AK/portal" ] && [ -z "$SSH_PASSWORD" ]; then
    echo "AVISO: SSH sem chaves autorizadas nem password: coloque a chave publica em data/ssh/authorized_keys"
fi

exec "$@"
