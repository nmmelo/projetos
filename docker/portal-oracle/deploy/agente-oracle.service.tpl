# /etc/systemd/system/@NOME@-agente-oracle.service
# Agente Oracle do portal: execucoes Oracle (shrink) em fila e agendadas, varias em simultaneo, isoladas
# do agente do portal (metricas, servicos, email). Gerado de deploy/agente-oracle.service.tpl
#
# Marcadores: @NOME@, @DIR@, @UTILIZADOR@, @SERVICOS@. Limites (CPU/memoria): instalar.sh --agente-recursos
[Unit]
Description=Portal Oracle - @NOME@ (agente Oracle: execucoes em fila e agendadas)
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
ExecStart=@DIR@/.venv/bin/python -m app.agente_oracle
SyslogIdentifier=@NOME@-agente-oracle
Restart=always
RestartSec=5
UMask=0027
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
