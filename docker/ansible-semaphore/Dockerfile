FROM ubuntu:26.04

ARG SSH_USER=admin
ARG SSH_UID=1026
ARG SSH_GID=100

ENV DEBIAN_FRONTEND=noninteractive \
    SSH_USER=${SSH_USER}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ansible git openssh-server openssh-client sudo ca-certificates \
        python3 python3-pip python3-venv curl nano tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd /etc/ansible && \
    userdel -r ubuntu 2>/dev/null || true

# Utilizador principal (SSH só com chave, sudo sem password).
# SSH_UID/SSH_GID iguais aos do anfitrião (no Synology: id <utilizador> → uid=1026 gid=100(users)),
# para os ficheiros dos playbooks terem o mesmo dono dentro e fora do contentor.
# Grupo "ansible": acesso de leitura ao ansible.cfg e à password do vault.
RUN set -e; \
    u="$(printf '%s' "${SSH_USER}" | tr -d '[:space:]')"; \
    uid="$(printf '%s' "${SSH_UID}" | tr -d '[:space:]')"; \
    gid="$(printf '%s' "${SSH_GID}" | tr -d '[:space:]')"; \
    echo "$u" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$' || { echo "SSH_USER inválido: '${SSH_USER}'"; exit 1; }; \
    echo "$uid" | grep -Eq '^[0-9]+$' || { echo "SSH_UID inválido: '${SSH_UID}'"; exit 1; }; \
    echo "$gid" | grep -Eq '^[0-9]+$' || { echo "SSH_GID inválido: '${SSH_GID}'"; exit 1; }; \
    getent group "$gid" >/dev/null || groupadd -g "$gid" grupo"$gid"; \
    groupadd ansible; \
    useradd -m -u "$uid" -g "$gid" -G sudo,ansible -s /bin/bash "$u"; \
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$u" > "/etc/sudoers.d/$u"; \
    chmod 440 "/etc/sudoers.d/$u"; \
    echo "$u" > /etc/ansible-user; \
    printf 'PubkeyAuthentication yes\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' \
        > /etc/ssh/sshd_config.d/00-contentor.conf; \
    rm -f /etc/ssh/ssh_host_*

COPY entrypoint.sh /entrypoint.sh
COPY ssh-utilizadores.sh /usr/local/sbin/ssh-utilizadores.sh
RUN chmod 755 /entrypoint.sh /usr/local/sbin/ssh-utilizadores.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
