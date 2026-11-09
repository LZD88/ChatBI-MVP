"""Initialize the local MySQL demo database and its read-only account."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pymysql  # type: ignore[import-untyped]


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = ROOT / "db"


def split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in sql:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote:
            current.append(char)
            escaped = True
            continue
        if char in {"'", '"', "`"}:
            quote = None if quote == char else (char if quote is None else quote)
        if char == ";" and quote is None:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def load_sql(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )


def initialize(*, host: str, port: int, admin_user: str, admin_password: str, reader_password: str) -> None:
    connection = pymysql.connect(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
    try:
        scripts = [
            load_sql(DB_DIR / "01_schema.sql"),
            load_sql(DB_DIR / "02_seed.sql"),
            load_sql(DB_DIR / "04_extended_schema.sql"),
            load_sql(DB_DIR / "05_extended_seed.sql"),
        ]
        escaped_password = connection.escape(reader_password)
        grants = load_sql(DB_DIR / "03_grants.sql").replace("__READER_PASSWORD__", escaped_password)
        scripts.append(grants)
        with connection.cursor() as cursor:
            for script in scripts:
                for statement in split_statements(script):
                    cursor.execute(statement)
        print("ChatBI demo database initialized successfully.")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("DB_ADMIN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_ADMIN_PORT", "3306")))
    parser.add_argument("--admin-user", default=os.getenv("DB_ADMIN_USER", "root"))
    parser.add_argument("--admin-password", default=os.getenv("DB_ADMIN_PASSWORD", ""))
    parser.add_argument("--reader-password", default=os.getenv("CHATBI_READER_PASSWORD"))
    args = parser.parse_args()
    if not args.reader_password:
        parser.error("--reader-password or CHATBI_READER_PASSWORD is required")
    initialize(
        host=args.host,
        port=args.port,
        admin_user=args.admin_user,
        admin_password=args.admin_password,
        reader_password=args.reader_password,
    )


if __name__ == "__main__":
    main()
