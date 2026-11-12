FROM ubuntu:26.04

ARG SSH_USER=nmmelo
ARG SSH_UID=1001

ENV DEBIAN_FRONTEND=noninteractive \
    SSH_USER=${SSH_USER}

RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx openssh-server ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd && \
    userdel -r ubuntu 2>/dev/null || true

# Utilizador SSH (sem password, só chave)
RUN useradd -m -u ${SSH_UID} -s /bin/bash ${SSH_USER} && \
    passwd -l ${SSH_USER}

# SSH: só autenticação por chave, sem root
RUN sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -ri 's/^#?PubkeyAuthentication .*/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    echo "AllowUsers ${SSH_USER}" >> /etc/ssh/sshd_config

COPY nginx.conf /etc/nginx/sites-available/default
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22 80
ENTRYPOINT ["/entrypoint.sh"]
