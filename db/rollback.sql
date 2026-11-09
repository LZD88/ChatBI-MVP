-- Destructive demo-only rollback. Review the explicit targets before running.
DROP USER IF EXISTS 'chatbi_reader'@'localhost';
DROP USER IF EXISTS 'chatbi_reader'@'127.0.0.1';
DROP DATABASE IF EXISTS chatbi_mvp;
