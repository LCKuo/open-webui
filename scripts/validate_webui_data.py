"""Fail closed when a local WebUI launcher points at the wrong SQLite data directory."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def allow_empty_database() -> bool:
    return os.getenv("WEBUI_ALLOW_EMPTY_DATABASE", "false").lower() == "true"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_webui_data.py <data-dir>", file=sys.stderr)
        return 2

    data_dir = Path(sys.argv[1]).expanduser().resolve()
    database = data_dir / "webui.db"

    if not database.is_file():
        if allow_empty_database():
            print(f"WebUI data guard: empty database explicitly allowed at {data_dir}")
            return 0
        print(
            f"WebUI data guard blocked startup: {database} does not exist.\n"
            "Refusing to create a blank WebUI instance. Set WEBUI_ALLOW_EMPTY_DATABASE=true "
            "only when intentionally creating a new instance.",
            file=sys.stderr,
        )
        return 1

    try:
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro",
            uri=True,
            timeout=10,
        )
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError(f"SQLite quick_check returned {integrity!r}")

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing_tables = {"user", "auth"} - tables
            if missing_tables:
                raise RuntimeError(
                    "missing required tables: " + ", ".join(sorted(missing_tables))
                )

            user_count = connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            active_auth_count = connection.execute(
                "SELECT COUNT(*) FROM auth WHERE active = 1"
            ).fetchone()[0]
        finally:
            connection.close()
    except (sqlite3.Error, RuntimeError) as exc:
        print(
            f"WebUI data guard blocked startup: {database} is not usable ({exc}).",
            file=sys.stderr,
        )
        return 1

    if user_count < 1 or active_auth_count < 1:
        if allow_empty_database():
            print(
                f"WebUI data guard: empty database explicitly allowed "
                f"(users={user_count}, active_auth={active_auth_count})."
            )
            return 0
        print(
            f"WebUI data guard blocked startup: {database} contains no usable accounts "
            f"(users={user_count}, active_auth={active_auth_count}).\n"
            "Refusing to show the create-admin screen for an existing deployment.",
            file=sys.stderr,
        )
        return 1

    print(
        f"WebUI data guard passed: {database} "
        f"(users={user_count}, active_auth={active_auth_count})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
