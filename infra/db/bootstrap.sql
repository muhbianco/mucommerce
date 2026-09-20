-- MariaDB bootstrap for the commerce platform (run ONCE by ops with a privileged account).
--
-- Principle: three users, least privilege.
--   mucommerce_migrate   DDL + DML on the two commerce databases (can CREATE DATABASE of that name)
--   mucommerce_app       DML only (runtime); INSERT/SELECT only on audit_log
--   mucommerce_analytics SELECT only on rpt_* views (phase 6)
--
-- Replace the placeholders before running. Restrict the host to the Docker bridge network
-- (e.g. '172.18.%') instead of '%' once the bind address is fixed. Never commit real passwords.

CREATE USER IF NOT EXISTS 'mucommerce_migrate'@'%' IDENTIFIED BY '<MIGRATE_PASSWORD>';
CREATE USER IF NOT EXISTS 'mucommerce_app'@'%' IDENTIFIED BY '<APP_PASSWORD>';
CREATE USER IF NOT EXISTS 'mucommerce_analytics'@'%' IDENTIFIED BY '<ANALYTICS_PASSWORD>';

-- Database-level grants are enough for `CREATE DATABASE IF NOT EXISTS mucommerce`.
GRANT ALL PRIVILEGES ON `mucommerce`.* TO 'mucommerce_migrate'@'%';
GRANT ALL PRIVILEGES ON `mucommerce_staging`.* TO 'mucommerce_migrate'@'%';

GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON `mucommerce`.* TO 'mucommerce_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON `mucommerce_staging`.* TO 'mucommerce_app'@'%';

FLUSH PRIVILEGES;

-- After the first `alembic upgrade head`, tighten audit_log to append-only for the runtime user:
--   REVOKE UPDATE, DELETE ON `mucommerce`.`audit_log` FROM 'mucommerce_app'@'%';
-- (MariaDB has no table-level REVOKE of a database-level grant; instead grant table by table
--  if you want strict append-only. See infra/db/README.md for the strict variant.)

-- Bind address: /etc/mysql/mariadb.conf.d/50-server.cnf
--   bind-address = 127.0.0.1,172.17.0.1   (loopback + docker0) instead of 0.0.0.0
-- and firewall 3306 from the public interface (ufw deny in on eth0 to any port 3306).
