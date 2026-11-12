FROM ubuntu:26.04

ARG SSH_USER=admin
ARG SSH_UID=1001

ENV DEBIAN_FRONTEND=noninteractive \
    SSH_USER=${SSH_USER}

RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx openssh-server sudo ca-certificates curl jq openssl && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd && \
    userdel -r ubuntu 2>/dev/null || true

# Utilizador SSH (sem password, só chave) com sudo sem password
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
COPY entrypoint.sh /entrypoint.sh
COPY tesla-setup.sh /usr/local/bin/tesla-setup
RUN chmod +x /entrypoint.sh /usr/local/bin/tesla-setup

EXPOSE 22 80
ENTRYPOINT ["/entrypoint.sh"]
