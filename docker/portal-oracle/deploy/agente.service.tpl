# /etc/systemd/system/@NOME@-agente.service
# Agente do Portal Oracle: execucoes Oracle em fila (continuam se o portal reiniciar), metricas do
# servidor para o modulo Sistema e start/stop/restart dos servicos. Gerado de deploy/agente.service.tpl
#
# Marcadores: @NOME@, @DIR@, @UTILIZADOR@, @SERVICOS@ (servicos mostrados/geridos no modulo Sistema)
# As acoes sobre servicos usam "sudo -n systemctl ..." com a regra /etc/sudoers.d/@NOME@-agente.
[Unit]
Description=Portal Oracle - @NOME@ (agente: execucoes, metricas e servicos)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@UTILIZADOR@
Group=@UTILIZADOR@
WorkingDirectory=@DIR@
EnvironmentFile=-@DIR@/.env
Environment=PYTHONUNBUFFERED=1
Environment=PORTAL_INSTANCIA=@NOME@
Environment="PORTAL_SERVICOS=@SERVICOS@"
ExecStartPre=+/usr/bin/chown -R @UTILIZADOR@:@UTILIZADOR@ @DIR@/data @DIR@/.venv
ExecStartPre=+/usr/bin/chmod 700 @DIR@/data
ExecStart=@DIR@/.venv/bin/python -m app.agente
SyslogIdentifier=@NOME@-agente
Restart=always
RestartSec=5
UMask=0027
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
