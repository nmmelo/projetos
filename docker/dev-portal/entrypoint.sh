#!/bin/bash
set -euo pipefail
install -d -m 755 /run/sshd
install -d -m 700 -o nmmelo -g nmmelo /home/nmmelo/.ssh
install -d -m 700 -o adminssc -g adminssc /home/adminssc/.ssh
ssh-keygen -l -f /run/secrets/nmmelo_public_key >/dev/null
install -m 600 -o nmmelo -g nmmelo /run/secrets/nmmelo_public_key /home/nmmelo/.ssh/authorized_keys
test -s /run/secrets/adminssc_private_key
install -m 600 -o adminssc -g adminssc /run/secrets/adminssc_private_key /home/adminssc/.ssh/adminssc
# Persistir a identidade SSH do servidor entre recriações.
for type in rsa ecdsa ed25519; do
    if [ ! -s "/var/lib/ssh-hostkeys/ssh_host_${type}_key" ]; then
        ssh-keygen -q -t "$type" -N '' -f "/var/lib/ssh-hostkeys/ssh_host_${type}_key"
    fi
    ln -sf "/var/lib/ssh-hostkeys/ssh_host_${type}_key" "/etc/ssh/ssh_host_${type}_key"
    ln -sf "/var/lib/ssh-hostkeys/ssh_host_${type}_key.pub" "/etc/ssh/ssh_host_${type}_key.pub"
done

# Portal: o código vem do bind mount ./portal (anfitrião).
if [ ! -e /opt/portal/app/app.py ]; then
    echo "ERRO: /opt/portal/app/app.py não existe. Coloca o app.py na pasta ./portal do anfitrião." >&2
    exit 1
fi

# Gunicorn (app Python) em 127.0.0.1:8000, logs em /var/log/gunicorn.
# --reload recarrega o código ao guardar ficheiros .py (desenvolvimento).
install -d -m 755 -o www-data -g www-data /var/log/gunicorn
/opt/portal/venv/bin/gunicorn \
    --chdir /opt/portal/app \
    --bind 127.0.0.1:8000 \
    --workers 3 \
    --reload \
    --reload-engine poll \
    --user www-data --group www-data \
    --access-logfile /var/log/gunicorn/access.log \
    --error-logfile /var/log/gunicorn/error.log \
    --pid /run/gunicorn.pid \
    --daemon \
    app:app

# Nginx (reverse proxy na porta 80).
nginx -t
nginx

/usr/sbin/sshd -t
exec /usr/sbin/sshd -D -e
