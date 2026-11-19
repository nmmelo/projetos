# geral

Configurações gerais de Linux.

| Pasta | Conteúdo |
|---|---|
| [`usb-wifi`](usb-wifi) | Internet numa VM Ubuntu (VirtualBox) através de uma pen LTE USB, com alternância entre a pen (trabalho) e o NAT (casa) |
| [`mount-islive`](mount-islive) | Valida de X em X minutos se os mounts do fstab respondem (escrita de `teste.txt`), refaz o mount e notifica por email SMTP quando volta a ficar ON |
| [`limpeza-logs-oracle`](limpeza-logs-oracle) | Assistente (whiptail, fundo azul) para gerir a limpeza de logs Oracle (alert.log, listener.log, traces, audit) por tamanho ou dias: rodar com cópia .gz, esvaziar ou apagar; cron ou logrotate e histórico de 365 dias |
| [`migrate-php-fpm`](migrate-php-fpm) | Migração do mod_php (Apache) para php-fpm por virtualhost: backup, diretivas `php_*` passadas para um pool por site, SetHandler nas portas 80/443, validação com a linha do erro, modo AUTO, `.delete` para os não usados e desativação do mod_php |
| [`extrair_pasta_git`](extrair_pasta_git) | Extrai apenas uma pasta de um repositório Git (HTTPS ou SSH) para um diretório local, sem histórico `.git`, com `sparse-checkout` e clone parcial num diretório temporário; perfis guardados para reutilizar as configurações |
