import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE.parent / "backend" / ".env")

CONFIG = {
    "host": os.getenv("SEEKDB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SEEKDB_PORT", "2881")),
    "user": os.getenv("SEEKDB_USER", "root"),
    "password": os.getenv("SEEKDB_PASSWORD", ""),
}


def run_file(conn, path: Path):
    statements = [s.strip() for s in path.read_text(encoding="utf-8").split(";") if s.strip()]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)


def main():
    conn = pymysql.connect(**CONFIG, charset="utf8mb4", autocommit=True, connect_timeout=5)
    try:
        run_file(conn, BASE / "schema.sql")
        run_file(conn, BASE / "seed.sql")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dify_app.drinks")
            drinks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM dify_app.rumors")
            rumors = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM dify_app.players")
            players = cur.fetchone()[0]
        print(f"initialized dify_app: drinks={drinks} rumors={rumors} players={players}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
