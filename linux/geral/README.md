# geral

Configurações gerais de Linux.

| Pasta | Conteúdo |
|---|---|
| [`usb-wifi`](usb-wifi) | Internet numa VM Ubuntu (VirtualBox) através de uma pen LTE USB, com alternância entre a pen (trabalho) e o NAT (casa) |
| [`mount-islive`](mount-islive) | Valida de X em X minutos se os mounts do fstab respondem (escrita de `teste.txt`), refaz o mount e notifica por email SMTP quando volta a ficar ON |
| [`limpeza-logs-oracle`](limpeza-logs-oracle) | Assistente (whiptail, fundo azul) para gerir a limpeza de logs Oracle (alert.log, listener.log, traces, audit) por tamanho ou dias: rodar com cópia .gz, esvaziar ou apagar; cron ou logrotate e histórico de 365 dias |
