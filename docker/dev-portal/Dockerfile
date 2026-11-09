FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/Lisbon

# Base + rede (ping, ip) + Nginx + build deps para drivers MariaDB
RUN apt-get update && apt-get install -y --no-install-recommends \
    ansible git openssh-server openssh-client sudo ca-certificates \
    python3 python3-pip python3-venv python3-dev curl nano tzdata \
    iputils-ping iproute2 dnsutils net-tools \
    nginx \
    build-essential pkg-config libmariadb-dev mariadb-client \
    && rm -rf /var/lib/apt/lists/*

# Utilizadores, sudo e SSH
RUN useradd -m -s /bin/bash nmmelo \
    && useradd -m -s /bin/bash adminssc \
    && printf 'nmmelo ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/nmmelo \
    && chmod 440 /etc/sudoers.d/nmmelo \
    && printf 'adminssc ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/adminssc \
    && chmod 440 /etc/sudoers.d/adminssc \
    && printf 'PubkeyAuthentication yes\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/00-container.conf \
    && rm -f /etc/ssh/ssh_host_* \
    && mkdir -p /workspace

# Ambiente Python do portal (venv evita o bloqueio PEP 668 do pip no sistema)
RUN mkdir -p /opt/portal/app \
    && python3 -m venv /opt/portal/venv \
    && /opt/portal/venv/bin/pip install --no-cache-dir --upgrade pip wheel \
    && /opt/portal/venv/bin/pip install --no-cache-dir \
       flask gunicorn mysqlclient pymysql sqlalchemy python-dotenv

# Nginx como reverse proxy para o Gunicorn (127.0.0.1:8000)
COPY portal-nginx.conf /etc/nginx/sites-available/portal
RUN rm -f /etc/nginx/sites-enabled/default \
    && ln -s /etc/nginx/sites-available/portal /etc/nginx/sites-enabled/portal

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh
WORKDIR /workspace
EXPOSE 22 80
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
