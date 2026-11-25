# My projects

Projetos feitos para resolver problemas. Cada pasta tem os ficheiros finais, um `README.md` com a explicação e a
utilização e um `CONVERSA.md` com o resumo do chat que deu origem ao projeto.

| Projeto | Pasta | Descrição |
|---|---|---|
| ansible-semaphore | [`docker`](docker) › [`ansible-semaphore`](docker/ansible-semaphore) | Ubuntu 26.04 com Ansible, Git e SSH (utilizador principal e extra, chave privada, `ansible.cfg` e vault fora do Git) + Semaphore UI, numa stack do Portainer a partir do Git com mounts para os playbooks |
| MariaDB em Docker | [`docker`](docker) › [`mariadbv1`](docker/mariadbv1) | MariaDB 11.4 em docker-compose com utilizador admin restrito a um IP, passwords em hash e pastas `data` e `conf` montadas |
| dev-portal | [`docker`](docker) › [`dev-portal`](docker/dev-portal) | Contentor Ubuntu 26.04 com Ansible, SSH e base para um portal Python: Nginx (reverse proxy) + Gunicorn com reload automático, código na pasta `portal/` do anfitrião, ferramentas de rede e drivers MariaDB |
| Portal Oracle | [`docker`](docker) › [`portal-oracle`](docker/portal-oracle) | Portal web em Python (Flask + Gunicorn + Nginx, Ubuntu 26.04) para gerir bases de dados Oracle: utilizadores e perfis, shrink de tablespaces, backups da configuração, armazenamento em SQLite, MariaDB ou PostgreSQL, aparência configurável (título, rodapé, ícone, temas Claro/Escuro/Oracle), módulos (Oracle, Sistema), agente do portal (execuções que sobrevivem a reinícios, métricas e serviços), SSH para o VS Code. Instalação sem Docker com o script `instalar.sh` (nome da instância à escolha, BD local ou remota com `pg_hba.conf`, Nginx com SSL) |
| tesla-key | [`docker`](docker) › [`tesla-key`](docker/tesla-key) | Contentor Ubuntu 26.04 (nginx + SSH) num Synology para alojar a chave pública da Tesla Fleet API em `tesla.<dominio>`, atrás do Proxy Reverso do DSM; inclui os passos de registo na Tesla, OAuth e emparelhamento |
| watchtower-ui | [`docker`](docker) › [`watchtower-ui`](docker/watchtower-ui) | Portal web (Ubuntu 26.04, nginx + Flask + SSH) para um Watchtower já existente: login com verificação humana, histórico de versões por contentor (SQLite no mount), atualizar todos ou por imagem, métricas, utilizadores, auditoria e logs; utilizador SSH com chave e sudo |
| Shrink de tablespaces | [`oracledb`](oracledb) › [`query`](oracledb/query) › [`shrink_tablespace`](oracledb/query/shrink_tablespace) | Script SQL*Plus que verifica se um tablespace (ou todos) pode ser reduzido, reorganiza os segmentos no fim dos datafiles (MOVE/REBUILD ONLINE) e faz o resize. Inclui notas sobre o SYSAUX |
| Monitor RMAN | [`oracledb`](oracledb) › [`scripts`](oracledb/scripts) › [`rman`](oracledb/scripts/rman) | Monitor pós-backup RMAN (Bash + Python): valida o log, apaga backups antigos quando a partição passa os 90% e envia email por SMTP com password cifrada |
| Oracle Wallet | [`oracledb`](oracledb) › [`scripts`](oracledb/scripts) › [`oracle-wallet`](oracledb/scripts/oracle-wallet) | Script Python que valida um Oracle Wallet sem credenciais (lê o `cwallet.sso`): validade de todos os certificados (dias e estado, código de saída para monitorização) e credenciais guardadas com password mascarada; `discover` dos wallets do servidor; gestão com `orapki`/`mkstore`: criar wallet, importar certificado por ficheiro ou texto `.crt`, remover, exportar, autoassinado, CSR, criar/alterar/remover credenciais, com backup antes |
| Internet na VM por USB | [`linux`](linux) › [`geral`](linux/geral) › [`usb-wifi`](linux/geral/usb-wifi) | VM Ubuntu no VirtualBox com internet por uma pen LTE USB (passthrough), netplan e funções `rede-trabalho` / `rede-casa` para alternar entre a pen e o NAT |
| Mount is live | [`linux`](linux) › [`geral`](linux/geral) › [`mount-islive`](linux/geral/mount-islive) | Testa de X em X minutos se os mounts do fstab respondem (escrita de `teste.txt` com timeout), refaz o mount e envia email SMTP com a hora da falha e do teste com sucesso. Setup interativo com `.env` e password cifrada |
| Migração mod_php → php-fpm | [`linux`](linux) › [`geral`](linux/geral) › [`migrate-php-fpm`](linux/geral/migrate-php-fpm) | Script interativo (um a um ou AUTO) que migra os virtualhosts ativos do Apache de mod_php para php-fpm: backup, diretivas `php_*` convertidas para um pool por site, SetHandler em 80/443, `.htaccess` → `.user.ini`, validação com a linha do erro, reload, log e desativação do mod_php |
| Limpeza de logs Oracle | [`linux`](linux) › [`geral`](linux/geral) › [`limpeza-logs-oracle`](linux/geral/limpeza-logs-oracle) | Assistente com fundo azul (whiptail) para limpar logs sem reiniciar a BD: por caminho e padrão (alert.log, listener.log, `*.trc`, `*.aud`) define tamanho máximo e/ou dias e a ação (rodar com cópia .gz, esvaziar ou apagar). Configuração tipo `.env`, simulação, crontab ou logrotate e histórico de 365 dias com resumo mensal |
| Extrair pasta de repositório Git | [`linux`](linux) › [`geral`](linux/geral) › [`extrair_pasta_git`](linux/geral/extrair_pasta_git) | Script interativo (ou com argumentos `-r -b -p -d`, ou por perfis guardados: `-c` sem perguntas, `-s`, `-e`, `-l`, `-x`) que extrai apenas uma pasta de um repositório Git (HTTPS ou SSH) para um diretório local, sem histórico `.git`: clone parcial com `sparse-checkout` num diretório temporário, validação da pasta na branch e limpeza no final |
| Limpeza do Docker | [`outros`](outros) › [`docker-cleanup`](outros/docker-cleanup) | Script Bash para o Agendador de tarefas do Synology (ou cron) que limpa imagens sem uso, build cache, redes sem uso e logs de containers grandes, com mais de 7 dias, sem apagar containers parados ou a correr nem volumes. Com `--dry-run`, `flock`, timeouts e registo |
| Contagem de linhas de código | [`outros`](outros) › [`count-code`](outros/count-code) | Script Bash/Python que conta as linhas de código, comentários e em branco de um projeto, por linguagem, pasta e ficheiro, com saída em tabela ou JSON |
| Estatísticas do GitHub | [`geral`](geral) › [`estatisticas_github`](geral/estatisticas_github) | Profile README do GitHub (repositório `utilizador/utilizador`) com badges, contactos e estatísticas dinâmicas do github-stats-extended; inclui exemplo pronto a usar |
| Remover linha com IP | [`geral`](geral) › [`remover-linha`](geral/remover-linha) | Script Python que pede um ficheiro e um IP e remove todas as linhas que o contenham (IP exato, com ou sem aspas), com confirmação, `--dry-run` e backup do último ficheiro |
| Password_crypt_manager | [`geral`](Password_crypt_manager) › [`Password_crypt_manager`](geral/Password_crypt_manager) | Utilitário em Bash para gestão de segredos e credenciais locais através de cifra simétrica com `openssl`.|
| MELCloud integrador | [`geral`](geral) › [`melcloud-integrador`](geral/melcloud-integrador) | Cliente Python (sem dependências) para a API não oficial do MELCloud clássico (Mitsubishi Electric): `list` dos equipamentos, `status` de unidades ar-ar e ar-água, `set` de power, modo, temperatura, ventilador e lâminas do ar condicionado; sessão guardada com novo login automático e aviso de bloqueio por excesso de pedidos |

## Estrutura

```
projetos/
├── docker/
│   ├── ansible-semaphore/          # Ansible + SSH + Semaphore UI (Portainer)
│   ├── dev-portal/                 # Ansible + Nginx + Gunicorn (base de portal Python)
│   ├── mariadbv1/                  # MariaDB em docker-compose
│   ├── portal-oracle/              # Portal Oracle (Python)
│   ├── tesla-key/                  # Chave pública da Tesla Fleet API (nginx + SSH)
│   └── watchtower-ui/              # Portal do Watchtower (login, histórico, auditoria)
├── linux/                          # Linux
│   ├── README.md
│   └── geral/                      # Configurações gerais
│       ├── README.md
│       ├── extrair_pasta_git/      # Extrair só uma pasta de um repositório Git
│       ├── limpeza-logs-oracle/    # Limpeza de logs Oracle (alert.log, traces, audit)
│       ├── migrate-php-fpm/        # Migração do Apache de mod_php para php-fpm
│       ├── mount-islive/           # Validação e remount automático de mounts
│       └── usb-wifi/               # Internet na VM por pen LTE USB
├── oracledb/                       # Oracle Database
│   ├── README.md
│   ├── query/                      # Queries e scripts SQL
│   │   ├── README.md
│   │   └── shrink_tablespace/      # Script de shrink de tablespaces
│   └── scripts/                    # Scripts de operação
│       ├── README.md
│       ├── oracle-wallet/          # Validação e gestão de Oracle Wallets
│       └── rman/                   # Monitor pós-backup RMAN
├── outros/                         # Scripts diversos
│   ├── README.md
│   ├── count-code/                 # Contagem de linhas de código
│   └── docker-cleanup/             # Limpeza periódica do Docker (Synology)
└── geral/                          # Guias gerais
    ├── README.md
    ├── estatisticas_github/        # Profile README com estatísticas
    └── remover-linha/              # Remover linhas com um IP de um ficheiro
    ├── melcloud-integrador/        # Cliente da API do MELCloud (Mitsubishi Electric)
    └── Password_crypt_manager/     # Utilitário em Bash para gestão de segredos e credenciais locais através de cifra simétrica com `openssl`.
```

## Licença

Este repositório usa a **[Apache License 2.0](LICENSE)**. Pode usar, copiar, alterar e redistribuir os projetos (também em trabalhos comerciais), desde que:

- mantenha a **referência ao autor original** (Nuno Melo), a nota de copyright e o ficheiro [NOTICE](NOTICE);
- inclua uma cópia da licença;
- indique nos ficheiros que alterou que foram modificados.

O trabalho é fornecido "tal como está", sem garantias.
