# /etc/systemd/system/@NOME@.service
# Portal Oracle (Gunicorn) - instalacao sem Docker. Gerado a partir de deploy/portal.service.tpl
#
# Marcadores (o instalar.sh substitui-os; numa instalacao manual, ver README):
#   @NOME@        nome da instancia (nome do servico)
#   @DIR@         pasta de instalacao
#   @UTILIZADOR@  utilizador de servico
#   @BIND@        endereco do Gunicorn, ex. 127.0.0.1:8000
[Unit]
Description=Portal Oracle - @NOME@ (Gunicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@UTILIZADOR@
Group=@UTILIZADOR@
WorkingDirectory=@DIR@
EnvironmentFile=-@DIR@/.env
Environment=PYTHONUNBUFFERED=1
# Gunicorn >= 25.1 cria um control socket (gunicornc) em $XDG_RUNTIME_DIR/gunicorn.ctl ou, sem
# essa variavel, em $HOME/.gunicorn/ - o HOME e a pasta do codigo, so de leitura para o servico.
# O systemd cria /run/@NOME@ (do utilizador de servico) ao arrancar e apaga-a ao parar.
RuntimeDirectory=@NOME@
RuntimeDirectoryMode=0750
Environment=XDG_RUNTIME_DIR=/run/@NOME@
# Antes de arrancar (como root, prefixo +): data/ e .venv sao do utilizador de servico. Corrige o caso
# de ficheiros copiados a mao como root (ex. sincronizacao do repositorio por cima da pasta).
ExecStartPre=+/usr/bin/chown -R @UTILIZADOR@:@UTILIZADOR@ @DIR@/data @DIR@/.venv
ExecStartPre=+/usr/bin/chmod 700 @DIR@/data
ExecStart=@DIR@/.venv/bin/gunicorn -c @DIR@/gunicorn.conf.py --bind @BIND@ wsgi:app
ExecReload=/bin/kill -s HUP $MAINPID
SyslogIdentifier=@NOME@
Restart=on-failure
RestartSec=5
UMask=0027
# As execucoes de shrink correm em threads: dar tempo para terminarem ao parar
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
