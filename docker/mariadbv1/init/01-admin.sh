#!/bin/bash
# Corre apenas quando ./data está vazio (primeira inicialização).
# NÃO pode ser executável (chmod -x): assim é carregado pelo entrypoint
# e a função docker_process_sql fica disponível (autentica como root sem password em claro).

docker_process_sql --database=mysql <<-EOSQL
  CREATE USER IF NOT EXISTS '${MARIADB_ADMIN_USER}'@'${MARIADB_ADMIN_HOST}'
    IDENTIFIED BY PASSWORD '${MARIADB_ADMIN_PASSWORD_HASH}';
  GRANT ALL PRIVILEGES ON *.* TO '${MARIADB_ADMIN_USER}'@'${MARIADB_ADMIN_HOST}'
    WITH GRANT OPTION;
  FLUSH PRIVILEGES;
EOSQL
