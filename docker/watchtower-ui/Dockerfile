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

# Utilizador SSH (sem password, só chave) com sudo sem password; também corre o portal
RUN useradd -m -u ${SSH_UID} -s /bin/bash -G sudo ${SSH_USER} && \
    passwd -l ${SSH_USER} && \
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "${SSH_USER}" > /etc/sudoers.d/${SSH_USER} && \
    chmod 440 /etc/sudoers.d/${SSH_USER}

# SSH: só autenticação por chave, sem root
RUN sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -ri 's/^#?PubkeyAuthentication .*/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    echo "AllowUsers ${SSH_USER}" >> /etc/ssh/sshd_config

COPY nginx.conf /etc/nginx/sites-available/default
COPY portal/ /opt/portal/
COPY entrypoint.sh /entrypoint.sh
COPY ssh-utilizadores.sh /usr/local/sbin/ssh-utilizadores.sh
RUN chmod +x /entrypoint.sh /usr/local/sbin/ssh-utilizadores.sh

EXPOSE 22 80
HEALTHCHECK --interval=1m --timeout=5s --start-period=30s CMD curl -fsS http://127.0.0.1/saude || exit 1
ENTRYPOINT ["/entrypoint.sh"]
