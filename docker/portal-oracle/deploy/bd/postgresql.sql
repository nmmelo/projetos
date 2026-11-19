-- Base de dados do portal em PostgreSQL.
-- Correr no servidor PostgreSQL como superutilizador:   sudo -u postgres psql -f postgresql.sql
--
-- O instalar.sh gera uma copia ja preenchida em <pasta do portal>/data/bd/postgresql.sql.
-- Numa preparacao manual, substitua os marcadores:
--   @BD@          nome da base de dados        (ex. portal_oracle)
--   @UTILIZADOR@  utilizador do portal         (ex. portal_oracle)
--   @PASSWORD@    password desse utilizador
--   @CLIENTE@     IP do servidor do portal     (so usado no pg_hba.conf, abaixo)

CREATE ROLE "@UTILIZADOR@" LOGIN PASSWORD '@PASSWORD@';
CREATE DATABASE "@BD@" OWNER "@UTILIZADOR@" ENCODING 'UTF8' TEMPLATE template0;
-- Se o utilizador ja existir: ALTER ROLE "@UTILIZADOR@" LOGIN PASSWORD '@PASSWORD@';

-- As tabelas sao criadas pelo portal na primeira ligacao.

-- ---------------------------------------------------------------- acesso remoto
-- 1) pg_hba.conf (caminho: SHOW hba_file;) - acrescentar ANTES das outras linhas "host":
--      host    @BD@    @UTILIZADOR@    @CLIENTE@/32    scram-sha-256
--    (se SHOW password_encryption; devolver md5, use md5 em vez de scram-sha-256)
--
-- 2) Escutar na rede (se SHOW listen_addresses; devolver localhost) - exige reiniciar:
--      ALTER SYSTEM SET listen_addresses = '*';
--      sudo systemctl restart postgresql
--
-- 3) Recarregar o pg_hba.conf:
--      SELECT pg_reload_conf();
--
-- 4) Firewall do servidor PostgreSQL:
--      sudo ufw allow from @CLIENTE@ to any port 5432 proto tcp
--
-- O script preparar-postgresql.sh (nesta pasta) faz os passos todos.
