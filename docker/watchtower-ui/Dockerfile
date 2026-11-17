FROM ubuntu:26.04

ARG SSH_USER=admin
ARG SSH_UID=1001

ENV DEBIAN_FRONTEND=noninteractive \
    SSH_USER=${SSH_USER} \
    DATA_PATH=/data \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx openssh-server sudo tzdata ca-certificates curl nano \
        python3 python3-flask gunicorn sqlite3 && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd && \
    userdel -r ubuntu 2>/dev/null || true

# Utilizador SSH (sem password, só chave) com sudo sem password; também corre o portal.
# Os valores são limpos de espaços (ex. espaço no fim da linha no .env) e o nome fica em /etc/portal-user.
RUN set -e; \
    u="$(printf '%s' "${SSH_USER}" | tr -d '[:space:]')"; \
    uid="$(printf '%s' "${SSH_UID}" | tr -d '[:space:]')"; \
    echo "$u" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$' || { echo "SSH_USER inválido: '${SSH_USER}'"; exit 1; }; \
    echo "$uid" | grep -Eq '^[0-9]+$' || { echo "SSH_UID inválido: '${SSH_UID}'"; exit 1; }; \
    useradd -m -u "$uid" -s /bin/bash -G sudo "$u"; \
    passwd -l "$u"; \
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$u" > "/etc/sudoers.d/$u"; \
    chmod 440 "/etc/sudoers.d/$u"; \
    echo "$u" > /etc/portal-user; \
    sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config; \
    sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config; \
    sed -ri 's/^#?PubkeyAuthentication .*/PubkeyAuthentication yes/' /etc/ssh/sshd_config; \
    echo "AllowUsers $u" >> /etc/ssh/sshd_config

COPY nginx.conf /etc/nginx/sites-available/default
COPY portal/ /opt/portal/
COPY entrypoint.sh /entrypoint.sh
COPY ssh-utilizadores.sh /usr/local/sbin/ssh-utilizadores.sh
RUN chmod +x /entrypoint.sh /usr/local/sbin/ssh-utilizadores.sh

EXPOSE 22 80
HEALTHCHECK --interval=1m --timeout=5s --start-period=30s CMD curl -fsS http://127.0.0.1/saude || exit 1
ENTRYPOINT ["/entrypoint.sh"]
