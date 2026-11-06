# Contentor unico (Ubuntu 26.04 LTS): Nginx + Gunicorn (Flask) + OpenSSH (VS Code Remote-SSH)
# + cron, geridos pelo supervisord. Nada do portal e copiado: tudo vem do mount /srv/portal.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Lisbon \
    LANG=C.UTF-8 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Pacotes do sistema. O 26.04 usa sudo-rs por omissao; instala-se o 'sudo' classico
# para garantir /etc/sudoers com @includedir /etc/sudoers.d (usado pelo entrypoint).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv \
      nginx openssh-server supervisor cron sudo \
      git iputils-ping iproute2 vim nano less curl wget ca-certificates procps tzdata unzip \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/nginx/sites-enabled/default \
 # a imagem ubuntu traz o utilizador 'ubuntu' com UID 1000: remover para o 'portal' ficar com 1000
 && (userdel -r ubuntu 2>/dev/null || true) \
 && groupadd sshusers \
 && useradd --create-home --uid 1000 --shell /bin/bash --groups sshusers portal \
 && usermod -p '*' portal \
 && git config --system --add safe.directory /srv/portal \
 # o grupo portal (SSH/VS Code) pode usar 'supervisorctl restart gunicorn'
 && sed -i 's/^chmod=0700.*/chmod=0770\nchown=root:portal/' /etc/supervisor/supervisord.conf

# Python 3.14 do sistema e protegido (PEP 668): os pacotes vao para um venv em /opt/venv.
# Se o requirements.txt (no mount) mudar, o entrypoint instala as diferencas no arranque.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && sha256sum /tmp/requirements.txt | cut -d' ' -f1 > /opt/venv/.requirements.sha256

# As configuracoes do sistema apontam para o mount: alterar docker/*.conf so exige reiniciar.
RUN ln -sf /srv/portal/docker/nginx.conf       /etc/nginx/conf.d/portal.conf \
 && ln -sf /srv/portal/docker/supervisord.conf /etc/supervisor/conf.d/portal.conf \
 && ln -sf /srv/portal/docker/sshd.conf        /etc/ssh/sshd_config.d/50-portal.conf

WORKDIR /srv/portal
EXPOSE 80 22
ENTRYPOINT ["/bin/bash", "/srv/portal/docker/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
