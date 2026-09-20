-- Development only (compose.dev.yml). Mirrors the production user split with throwaway passwords.
CREATE USER IF NOT EXISTS 'mucommerce_app'@'%' IDENTIFIED BY 'app';
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON `mucommerce`.* TO 'mucommerce_app'@'%';
GRANT ALL PRIVILEGES ON `mucommerce_test`.* TO 'mucommerce_migrate'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON `mucommerce_test`.* TO 'mucommerce_app'@'%';
FLUSH PRIVILEGES;
