# My projects

Projetos feitos para resolver problemas. Cada pasta tem os ficheiros finais, um `README.md` com a explicação e a
utilização e um `CONVERSA.md` com o resumo do chat que deu origem ao projeto.

| Projeto | Pasta | Descrição |
|---|---|---|
| MariaDB em Docker | [`docker`](docker) › [`mariadbv1`](docker/mariadbv1) | MariaDB 11.4 em docker-compose com utilizador admin restrito a um IP, passwords em hash e pastas `data` e `conf` montadas |
| dev-portal | [`docker`](docker) › [`dev-portal`](docker/dev-portal) | Contentor Ubuntu 26.04 com Ansible, SSH e base para um portal Python: Nginx (reverse proxy) + Gunicorn com reload automático, código na pasta `portal/` do anfitrião, ferramentas de rede e drivers MariaDB |
| Portal Oracle | [`docker`](docker) › [`portal-oracle`](docker/portal-oracle) | Portal web em Python (Flask + Gunicorn + Nginx, Ubuntu 26.04) para gerir bases de dados Oracle: utilizadores e perfis, shrink de tablespaces, backups da configuração, armazenamento em SQLite, MariaDB ou PostgreSQL, SSH para o VS Code |
| Shrink de tablespaces | [`oracledb`](oracledb) › [`query`](oracledb/query) › [`shrink_tablespace`](oracledb/query/shrink_tablespace) | Script SQL*Plus que verifica se um tablespace (ou todos) pode ser reduzido, reorganiza os segmentos no fim dos datafiles (MOVE/REBUILD ONLINE) e faz o resize. Inclui notas sobre o SYSAUX |
| Monitor RMAN | [`oracledb`](oracledb) › [`scripts`](oracledb/scripts) › [`rman`](oracledb/scripts/rman) | Monitor pós-backup RMAN (Bash + Python): valida o log, apaga backups antigos quando a partição passa os 90% e envia email por SMTP com password cifrada |
| Internet na VM por USB | [`linux`](linux) › [`geral`](linux/geral) › [`usb-wifi`](linux/geral/usb-wifi) | VM Ubuntu no VirtualBox com internet por uma pen LTE USB (passthrough), netplan e funções `rede-trabalho` / `rede-casa` para alternar entre a pen e o NAT |
| Mount is live | [`linux`](linux) › [`geral`](linux/geral) › [`mount-islive`](linux/geral/mount-islive) | Testa de X em X minutos se os mounts do fstab respondem (escrita de `teste.txt` com timeout), refaz o mount e envia email SMTP com a hora da falha e do teste com sucesso. Setup interativo com `.env` e password cifrada |
| Contagem de linhas de código | [`outros`](outros) › [`count-code`](outros/count-code) | Script Bash/Python que conta as linhas de código, comentários e em branco de um projeto, por linguagem, pasta e ficheiro, com saída em tabela ou JSON |
| Estatísticas do GitHub | [`geral`](geral) › [`estatisticas_github`](geral/estatisticas_github) | Profile README do GitHub (repositório `nmmelo/nmmelo`) com badges, contactos e estatísticas dinâmicas do github-stats-extended; inclui exemplo pronto a usar |
| Remover linha com IP | [`geral`](geral) › [`remover-linha`](geral/remover-linha) | Script Python que pede um ficheiro e um IP e remove todas as linhas que o contenham (IP exato, com ou sem aspas), com confirmação, `--dry-run` e backup do último ficheiro |
| Remover linha com IP | [`geral`](Password_crypt_manager) › [`Password_crypt_manager`](geral/Password_crypt_manager) | Script Password_crypt_manager para gestão de passwords |

## Estrutura

```
projetos/
├── docker/
│   ├── dev-portal/                 # Ansible + Nginx + Gunicorn (base de portal Python)
│   ├── mariadbv1/                  # MariaDB em docker-compose
│   └── portal-oracle/              # Portal Oracle (Python)
├── linux/                          # Linux
│   ├── README.md
│   └── geral/                      # Configurações gerais
│       ├── README.md
│       ├── mount-islive/           # Validação e remount automático de mounts
│       └── usb-wifi/               # Internet na VM por pen LTE USB
├── oracledb/                       # Oracle Database
│   ├── README.md
│   ├── query/                      # Queries e scripts SQL
│   │   ├── README.md
│   │   └── shrink_tablespace/      # Script de shrink de tablespaces
│   └── scripts/                    # Scripts de operação
│       ├── README.md
│       └── rman/                   # Monitor pós-backup RMAN
├── outros/                         # Scripts diversos
│   ├── README.md
│   └── count-code/                 # Contagem de linhas de código
└── geral/                          # Guias gerais
    ├── README.md
    ├── estatisticas_github/        # Profile README com estatísticas
    └── remover-linha/              # Remover linhas com um IP de um ficheiro
    └── Password_crypt_manager/     # Para fazer gestão de password encriptadas
```
