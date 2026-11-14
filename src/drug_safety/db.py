"""Database connection and migrations for Person B's local work.

Usage:
    python -m drug_safety.db migrate     # apply warehouse_proposal/*.sql in order (safe to rerun)

Migrations are recorded in public.schema_migrations, so each file runs once. In the main repo,
Person A's `migrate` service replaces this.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from drug_safety.config import get_settings

MIGRATIONS_DIR = Path("warehouse_proposal")


def connect(url: str | None = None, vector: bool = True) -> psycopg.Connection:
    conn = psycopg.connect(url or get_settings().database_url)
    if vector:
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:  # extension not created yet (before first migrate)
            conn.rollback()
    return conn


def migrate(url: str | None = None) -> list[str]:
    applied = []
    with connect(url, vector=False) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations "
            "(filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT filename FROM public.schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO public.schema_migrations (filename) VALUES (%s)", [path.name])
            applied.append(path.name)
        conn.commit()
    return applied


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args != ["migrate"]:
        print("usage: python -m drug_safety.db migrate")
        return 2
    applied = migrate()
    print("applied:", ", ".join(applied) if applied else "nothing (already up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
