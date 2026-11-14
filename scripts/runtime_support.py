"""Launcher checks and explicit demo bootstrap, without secret output."""
import argparse
import json
import os

import pymysql

from chatbi.settings import Settings
from chatbi.infrastructure.runtime_version import source_version
from scripts.init_database import initialize


def check_database(settings: Settings) -> dict:
    connection = pymysql.connect(host=settings.db_host, port=settings.db_port, user=settings.db_user,
                                 password=settings.db_password.get_secret_value() if settings.db_password else "",
                                 database=settings.db_name, connect_timeout=3, read_timeout=3)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'", (settings.db_name,))
            tables = cursor.fetchone()[0]
            cursor.execute("SHOW GRANTS")
            grants = " ".join(row[0] for row in cursor.fetchall()).upper()
            readonly = "GRANT SELECT ON" in grants and not any(token in grants for token in ("ALL PRIVILEGES", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "GRANT OPTION"))
            return {"database": settings.db_name, "tables": tables, "readonly": readonly, "ready": tables == 30 and readonly}
    finally:
        connection.close()


def bootstrap(settings: Settings) -> None:
    if settings.db_name != "chatbi_mvp" or settings.db_user != "chatbi_reader":
        raise ValueError("Bootstrap only supports named demo database and reader")
    password = settings.db_password.get_secret_value() if settings.db_password else ""
    if not password:
        raise ValueError("Reader password required")
    initialize(host=settings.db_host, port=settings.db_port, admin_user=os.getenv("DB_ADMIN_USER", "root"),
               admin_password=os.getenv("DB_ADMIN_PASSWORD", ""), reader_password=password)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["db-check", "bootstrap", "version"])
    args = parser.parse_args()
    try:
        if args.command == "version":
            print(source_version())
        elif args.command == "bootstrap":
            bootstrap(Settings())
        else:
            result = check_database(Settings())
            print(json.dumps(result))
            raise SystemExit(0 if result["ready"] else 2)
    except Exception as exc:
        print(json.dumps({"ready": False, "error": type(exc).__name__, "hint": "检查数据库端口、账号、密码及30张演示表"}, ensure_ascii=False))
        raise SystemExit(2)
