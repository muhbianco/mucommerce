-- MariaDB bootstrap for the commerce platform (run ONCE by ops with a privileged account).
--
-- Principle: one user per job, least privilege. No staging database (ADR 0007).
--   mucommerce_migrate   DDL + DML on `mucommerce` (can CREATE DATABASE of that name)
--   mucommerce_app       DML only (runtime); INSERT/SELECT only on audit_log
--   mucommerce_analytics SELECT only on rpt_* views (phase 6)
--   mucommerce_backup    read + lock for mariadb-dump, host-only (infra/backup/mariadb_backup.sh)
--
-- Replace the placeholders before running. Restrict the host to the Docker bridge network
-- (e.g. '172.18.%') instead of '%' once the bind address is fixed. Never commit real passwords.

CREATE USER IF NOT EXISTS 'mucommerce_migrate'@'%' IDENTIFIED BY '<MIGRATE_PASSWORD>';
CREATE USER IF NOT EXISTS 'mucommerce_app'@'%' IDENTIFIED BY '<APP_PASSWORD>';
CREATE USER IF NOT EXISTS 'mucommerce_analytics'@'%' IDENTIFIED BY '<ANALYTICS_PASSWORD>';
CREATE USER IF NOT EXISTS 'mucommerce_backup'@'localhost' IDENTIFIED BY '<BACKUP_PASSWORD>';

-- Database-level grants are enough for `CREATE DATABASE IF NOT EXISTS mucommerce`.
GRANT ALL PRIVILEGES ON `mucommerce`.* TO 'mucommerce_migrate'@'%';

GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON `mucommerce`.* TO 'mucommerce_app'@'%';

GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES ON `mucommerce`.* TO 'mucommerce_backup'@'localhost';

FLUSH PRIVILEGES;

-- After the first `alembic upgrade head`, tighten audit_log to append-only for the runtime user:
--   REVOKE UPDATE, DELETE ON `mucommerce`.`audit_log` FROM 'mucommerce_app'@'%';
-- (MariaDB has no table-level REVOKE of a database-level grant; instead grant table by table
--  if you want strict append-only. See infra/db/README.md for the strict variant.)

-- Network: 3306 is closed on the public interface by the nft table `inet mb_guard` (unit
-- mb-guard, see .claude/ops/hel1-mariadb-hardening.sh in the workspace); containers use
-- host.docker.internal / 172.17.0.1 and admins an SSH tunnel.
--
-- Existing installs created before ADR 0007 can drop the staging grants:
--   REVOKE ALL PRIVILEGES ON `mucommerce_staging`.* FROM 'mucommerce_migrate'@'%';
--   REVOKE ALL PRIVILEGES ON `mucommerce_staging`.* FROM 'mucommerce_app'@'%';
