from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


SEARCH_CONFIG = {
    "web.search.enable": True,
    "web.search.engine": "searxng",
    "web.search.searxng_query_url": "http://127.0.0.1:8082/search",
    "web.search.searxng_language": "zh-TW",
    "web.search.result_count": 8,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure WebUI to use the private SearXNG tunnel.")
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--restore", action="store_true")
    return parser.parse_args()


def decode_value(raw):
    if raw is None:
        return None
    if not isinstance(raw, (str, bytes, bytearray)):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main() -> int:
    args = parse_args()
    database = args.data_dir.resolve() / "webui.db"
    if not database.is_file():
        raise SystemExit(f"WebUI database does not exist: {database}")

    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database, timeout=30) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='config'"
        ).fetchone()
        if not table:
            raise SystemExit("WebUI config table is missing. Run database migrations first.")

        if args.restore:
            if not args.backup.is_file():
                raise SystemExit(f"Search configuration backup does not exist: {args.backup}")
            previous = json.loads(args.backup.read_text(encoding="utf-8"))
            now = int(time.time())
            for key, item in previous.items():
                if item.get("exists"):
                    connection.execute(
                        """
                        INSERT INTO config (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (key, json.dumps(item.get("value"), ensure_ascii=False), now),
                    )
                else:
                    connection.execute("DELETE FROM config WHERE key = ?", (key,))
            connection.commit()
            print(f"Restored previous web search configuration for {database}")
            return 0

        if not args.backup.exists():
            previous = {}
            for key in SEARCH_CONFIG:
                row = connection.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
                previous[key] = {"exists": bool(row), "value": decode_value(row[0]) if row else None}
            args.backup.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        now = int(time.time())
        for key, value in SEARCH_CONFIG.items():
            connection.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        connection.commit()

    print(f"Configured private SearXNG for {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
