-- This file is a template. scripts/init_database.py replaces the token below safely.
CREATE USER IF NOT EXISTS 'chatbi_reader'@'localhost' IDENTIFIED BY __READER_PASSWORD__;
ALTER USER 'chatbi_reader'@'localhost' IDENTIFIED BY __READER_PASSWORD__;
GRANT SELECT ON chatbi_mvp.* TO 'chatbi_reader'@'localhost';

CREATE USER IF NOT EXISTS 'chatbi_reader'@'127.0.0.1' IDENTIFIED BY __READER_PASSWORD__;
ALTER USER 'chatbi_reader'@'127.0.0.1' IDENTIFIED BY __READER_PASSWORD__;
GRANT SELECT ON chatbi_mvp.* TO 'chatbi_reader'@'127.0.0.1';
FLUSH PRIVILEGES;
