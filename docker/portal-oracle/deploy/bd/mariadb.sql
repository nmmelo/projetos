-- Base de dados do portal em MariaDB / MySQL.
-- Correr no servidor MariaDB como root:   sudo mysql < mariadb.sql
--
-- O instalar.sh gera uma copia ja preenchida em <pasta do portal>/data/bd/mariadb.sql.
-- Numa preparacao manual, substitua os marcadores:
--   @BD@          nome da base de dados        (ex. portal_oracle)
--   @UTILIZADOR@  utilizador do portal         (ex. portal_oracle)
--   @PASSWORD@    password desse utilizador
--   @CLIENTE@     IP do servidor do portal (de onde chega a ligacao)

CREATE DATABASE IF NOT EXISTS `@BD@` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '@UTILIZADOR@'@'@CLIENTE@' IDENTIFIED BY '@PASSWORD@';
ALTER USER '@UTILIZADOR@'@'@CLIENTE@' IDENTIFIED BY '@PASSWORD@';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP
   ON `@BD@`.* TO '@UTILIZADOR@'@'@CLIENTE@';

-- As tabelas sao criadas pelo portal na primeira ligacao.

-- ---------------------------------------------------------------- acesso remoto
-- 1) O MariaDB tem de escutar na rede. Se SELECT @@bind_address; devolver 127.0.0.1,
--    crie /etc/mysql/mariadb.conf.d/99-acesso-remoto.cnf (RHEL: /etc/my.cnf.d/) com:
--      [mysqld]
--      bind-address = 0.0.0.0
--    e reinicie:  sudo systemctl restart mariadb
--
-- 2) Firewall do servidor MariaDB:
--      sudo ufw allow from @CLIENTE@ to any port 3306 proto tcp
--
-- O script preparar-mariadb.sh (nesta pasta) faz os passos todos.
