# claude

Projetos feitos com o Claude. Cada pasta tem os ficheiros finais, um `README.md` com a explicação e a
utilização e um `CONVERSA.md` com o resumo do chat que deu origem ao projeto.

| Projeto | Pasta | Descrição |
|---|---|---|
| MariaDB em Docker | [`docker`](docker) › [`mariadbv1`](docker/mariadbv1) | MariaDB 11.4 em docker-compose com utilizador admin restrito a um IP, passwords em hash e pastas `data` e `conf` montadas |
| Portal Oracle | [`docker`](docker) › [`portal-oracle`](docker/portal-oracle) | Portal web em Python (Flask + Gunicorn + Nginx, Ubuntu 26.04) para gerir bases de dados Oracle: utilizadores e perfis, shrink de tablespaces, backups da configuração, armazenamento em SQLite, MariaDB ou PostgreSQL, SSH para o VS Code |
| Shrink de tablespaces | [`oracledb`](oracledb) › [`query`](oracledb/query) › [`shrink_tablespace`](oracledb/query/shrink_tablespace) | Script SQL*Plus que verifica se um tablespace (ou todos) pode ser reduzido, reorganiza os segmentos no fim dos datafiles (MOVE/REBUILD ONLINE) e faz o resize. Inclui notas sobre o SYSAUX |

## Estrutura

```
claude/
├── docker/
│   ├── mariadbv1/                  # MariaDB em docker-compose
│   └── portal-oracle/              # Portal Oracle (Python)
└── oracledb/                       # Oracle Database
    ├── README.md
    └── query/                      # Queries e scripts SQL
        ├── README.md
        └── shrink_tablespace/      # Script de shrink de tablespaces
```
