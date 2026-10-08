"""Create/update the least-privilege Postgres role the MCP server connects as.

Idempotent: safe to re-run after every scripts/load_data.py run, since
to_sql(if_exists="replace") drops and recreates raw.* tables -- the
ALTER DEFAULT PRIVILEGES here is what makes the readonly grant survive that
without needing to be reapplied by hand.

Uses psycopg directly (not SQLAlchemy) so role/schema names can go through
psycopg.sql.Identifier for correct quoting -- these aren't user input, but DDL
identifiers can't be bound as query parameters either way.

    uv run python scripts/setup_db_roles.py
"""

import logging
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "raw"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("setup_db_roles")


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    app_user = os.environ["POSTGRES_USER"]
    readonly_user = os.environ["POSTGRES_READONLY_USER"]
    readonly_password = os.environ["POSTGRES_READONLY_PASSWORD"]

    conn = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=app_user,
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
    )
    with conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (readonly_user,))
        exists = cur.fetchone() is not None

        # Postgres doesn't allow a bind parameter in the PASSWORD clause of
        # CREATE/ALTER ROLE -- it's not a prepared-statement-friendly grammar
        # position -- so this uses sql.Literal for safe quoting instead.
        if exists:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(readonly_user), sql.Literal(readonly_password)
                )
            )
            logger.info("Updated password for existing role '%s'", readonly_user)
        else:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(readonly_user), sql.Literal(readonly_password)
                )
            )
            logger.info("Created role '%s'", readonly_user)

        cur.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(readonly_user)
            )
        )
        cur.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(readonly_user)
            )
        )
        cur.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}"
            ).format(
                sql.Identifier(app_user), sql.Identifier(SCHEMA), sql.Identifier(readonly_user)
            )
        )
        logger.info(
            "Granted SELECT on schema '%s' to '%s' (including future tables created by '%s')",
            SCHEMA,
            readonly_user,
            app_user,
        )
    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
