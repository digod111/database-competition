import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

DB_CONFIG = {
    "host": os.getenv("SEEKDB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SEEKDB_PORT", "2881")),
    "user": os.getenv("SEEKDB_USER", "root"),
    "password": os.getenv("SEEKDB_PASSWORD", ""),
    "database": os.getenv("SEEKDB_DATABASE", "dify_app"),
}


def get_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        **DB_CONFIG,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=5,
        read_timeout=10,
    )
