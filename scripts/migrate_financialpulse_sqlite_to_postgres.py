#!/usr/bin/env python3
"""
One-time migration: FinancialPulse SQLite (financialpulse.db) -> PostgreSQL.

Run from YOUR PC (not on Render) using the Postgres EXTERNAL URL from the
Render dashboard (internal hostnames only work inside Render's network).

Does NOT touch MarketMinds `users` or other non–FinancialPulse tables.

Usage (PowerShell):
  cd <repo root>   # folder that contains `scripts\\` and `financial_sentiment_v8\\`
  .\\.venv\\Scripts\\activate
  pip install psycopg2-binary
  $env:MARKETMINDS_DATABASE_URL = "postgresql://USER:PASS@HOST/DB?sslmode=require"
  python scripts/migrate_financialpulse_sqlite_to_postgres.py `
    --sqlite "financial_sentiment_v8/financial_sentiment_v8/fyp_enhanced/financialpulse.db" `
    --truncate-fp --yes

Render's web app may write the same Postgres while you migrate; that can cause
deadlocks on large inserts. This script commits in batches and retries deadlocks.
If migration still fails, temporarily scale the Render **Web Service** to 0 instances,
run the script, then scale back up.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# FinancialPulse tables only, children before parents for TRUNCATE CASCADE.
_FP_TABLES_TRUNCATE = (
    "signal_backtests",
    "generated_signals",
    "alert_logs",
    "sentiment_alerts",
    "sentiment_trends",
    "price_snapshots",
    "price_history",
    "news_articles",
    "backtest_results",
    "evaluation_snapshots",
)

# Insert order respects FKs.
_FP_TABLES_INSERT = (
    "news_articles",
    "price_snapshots",
    "price_history",
    "sentiment_alerts",
    "sentiment_trends",
    "alert_logs",
    "generated_signals",
    "signal_backtests",
    "backtest_results",
    "evaluation_snapshots",
)

# libpq URI query keys only (see PostgreSQL "Connection URIs"); drop anything else.
_PG_URI_QUERY_KEYS = frozenset(
    k.lower()
    for k in (
        "host",
        "hostaddr",
        "port",
        "dbname",
        "user",
        "password",
        "passfile",
        "connect_timeout",
        "client_encoding",
        "options",
        "application_name",
        "fallback_application_name",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
        "tcp_user_timeout",
        "replication",
        "gssencmode",
        "sslmode",
        "ssl",
        "sslcompression",
        "sslcert",
        "sslkey",
        "sslrootcert",
        "sslcrl",
        "sslcrldir",
        "sslsni",
        "requirepeer",
        "ssl_min_protocol_version",
        "ssl_max_protocol_version",
        "krbsrvname",
        "gsslib",
        "service",
        "target_session_attrs",
        "load_balance_hosts",
    )
)


def _normalize_raw_database_url(raw: str) -> str:
    """Undo common .env paste mistakes before urlsplit/psycopg2."""
    u = (raw or "").strip().strip('"').strip("'")
    for prefix in ("MARKETMINDS_DATABASE_URL=", "DATABASE_URL=", "FINANCIALPULSE_DATABASE_URL="):
        while u.upper().startswith(prefix.upper()):
            u = u[len(prefix) :].strip()
    return u


def _pg_url(raw: str) -> str:
    u = _normalize_raw_database_url(raw).replace("postgres://", "postgresql://", 1)
    try:
        parts = urlsplit(u)
        q_pairs = parse_qsl(parts.query, keep_blank_values=True)
        q: dict[str, str] = {}
        dropped: list[str] = []
        for k, v in q_pairs:
            kl = k.lower()
            if kl in _PG_URI_QUERY_KEYS:
                q[kl] = v
            else:
                dropped.append(k)
        if dropped:
            print(
                f"[warn] Removed invalid DSN query key(s) (not libpq options): {', '.join(dropped)}",
                flush=True,
            )
        if "sslmode" not in q and "ssl" not in q:
            q["sslmode"] = "require"
        if "connect_timeout" not in q:
            q["connect_timeout"] = "30"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    except Exception:
        return u


def _sqlite_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cur.fetchall()]


def _pg_columns(pg, table: str) -> set[str]:
    with pg.cursor() as c:
        c.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return {r[0] for r in c.fetchall()}


def _copy_table(
    sl: sqlite3.Connection,
    pg,
    table: str,
    truncate_mode: bool,
    batch_size: int = 200,
    deadlock_retries: int = 15,
) -> int:
    s_cur = sl.cursor()
    s_cols = _sqlite_columns(s_cur, table)
    pg_cols = _pg_columns(pg, table)
    cols = [c for c in s_cols if c in pg_cols]
    if not cols:
        return 0
    col_sql = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))

    if truncate_mode:
        sql = f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders})'
    else:
        # Idempotent when PK/unique exists (use only when Postgres FP tables are empty or you understand skips).
        if table == "news_articles":
            conflict = "ON CONFLICT (id) DO NOTHING"
        elif table == "price_history":
            conflict = "ON CONFLICT (asset_key, date) DO NOTHING"
        elif table == "sentiment_trends":
            conflict = "ON CONFLICT (asset_key, hour_bucket) DO NOTHING"
        elif table == "generated_signals":
            conflict = "ON CONFLICT (signal_uid) DO NOTHING"
        elif table == "signal_backtests":
            conflict = "ON CONFLICT (signal_id) DO NOTHING"
        elif table in (
            "price_snapshots",
            "sentiment_alerts",
            "alert_logs",
            "backtest_results",
            "evaluation_snapshots",
        ):
            conflict = "ON CONFLICT (id) DO NOTHING"
        else:
            conflict = ""
        sql = f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders}) {conflict}'.strip()

    from psycopg2 import errors as pg_errors

    s_cur.execute(f'SELECT {col_sql} FROM "{table}"')
    rows = s_cur.fetchall()
    if not rows:
        return 0

    total = len(rows)
    bs = max(50, min(batch_size, 2000))
    inserted = 0
    for start in range(0, total, bs):
        chunk = rows[start : start + bs]
        for attempt in range(deadlock_retries):
            try:
                with pg.cursor() as p:
                    p.executemany(sql, chunk)
                pg.commit()
                inserted += len(chunk)
                break
            except pg_errors.DeadlockDetected:
                pg.rollback()
                if attempt + 1 >= deadlock_retries:
                    raise
                time.sleep(min(2.0, 0.08 * (2**attempt)))
            except Exception:
                pg.rollback()
                raise
    return total


def _reset_serial(pg, table: str) -> None:
    with pg.cursor() as c:
        c.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, "id"))
        row = c.fetchone()
        seq = row[0] if row else None
        if not seq:
            return
        c.execute(f'SELECT COALESCE(MAX(id), 1) FROM "{table}"')
        mx = c.fetchone()[0]
        c.execute("SELECT setval(%s, %s, true)", (seq, mx))


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        from dotenv import load_dotenv

        load_dotenv(repo_root / ".env")
    except ImportError:
        pass

    p = argparse.ArgumentParser(description="Migrate FinancialPulse SQLite to Postgres.")
    p.add_argument(
        "--sqlite",
        default=os.environ.get("FINANCIALPULSE_SQLITE_PATH", "").strip()
        or str(
            Path(__file__).resolve().parent.parent
            / "financial_sentiment_v8"
            / "financial_sentiment_v8"
            / "fyp_enhanced"
            / "financialpulse.db"
        ),
        help="Path to financialpulse.db",
    )
    p.add_argument(
        "--postgres-url",
        default=(
            os.environ.get("FINANCIALPULSE_DATABASE_URL")
            or os.environ.get("MARKETMINDS_DATABASE_URL")
            or os.environ.get("DATABASE_URL")
            or ""
        ).strip(),
        help="Postgres URL (use Render EXTERNAL from your PC). Env: MARKETMINDS_DATABASE_URL / DATABASE_URL.",
    )
    p.add_argument(
        "--truncate-fp",
        action="store_true",
        help="TRUNCATE only FinancialPulse tables on Postgres then copy (recommended for first migration).",
    )
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation before TRUNCATE.")
    p.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows per INSERT batch (smaller + commits = fewer deadlocks vs live app). Default 200.",
    )
    args = p.parse_args()

    sqlite_path = Path(args.sqlite).expanduser().resolve()
    if not sqlite_path.is_file():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 1

    pg_url = args.postgres_url
    if not pg_url:
        print(
            "ERROR: Set --postgres-url or MARKETMINDS_DATABASE_URL / DATABASE_URL "
            "(Render external Postgres URL from dashboard).",
            file=sys.stderr,
        )
        return 1

    pg_url = _pg_url(pg_url)

    try:
        import psycopg2
    except ImportError:
        print("ERROR: pip install psycopg2-binary", file=sys.stderr)
        return 1

    if args.truncate_fp and not args.yes:
        print(
            "--truncate-fp will DELETE all rows in FinancialPulse tables on Postgres "
            f"(tables: {', '.join(_FP_TABLES_TRUNCATE)}).\n"
            "MarketMinds `users` is NOT touched.\n"
            "Re-run with --yes to confirm."
        )
        return 1

    sl = sqlite3.connect(str(sqlite_path))
    sl.row_factory = sqlite3.Row

    pg = psycopg2.connect(pg_url)
    pg.autocommit = False

    try:
        if args.truncate_fp:
            with pg.cursor() as c:
                # One TRUNCATE lists all FP tables; CASCADE clears FKs among them only.
                names = ", ".join(f'"{t}"' for t in _FP_TABLES_TRUNCATE)
                c.execute(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE;")
            pg.commit()
            print("[ok] Truncated FinancialPulse tables on Postgres.", flush=True)

        total = 0
        for tbl in _FP_TABLES_INSERT:
            n = _copy_table(
                sl,
                pg,
                tbl,
                truncate_mode=args.truncate_fp,
                batch_size=args.batch_size,
            )
            total += n
            print(f"[ok] {tbl}: {n} rows", flush=True)

        pg.commit()

        if args.truncate_fp:
            for tbl in (
                "price_snapshots",
                "price_history",
                "sentiment_alerts",
                "alert_logs",
                "generated_signals",
                "signal_backtests",
                "backtest_results",
                "evaluation_snapshots",
            ):
                try:
                    _reset_serial(pg, tbl)
                except Exception as e:
                    print(f"[warn] sequence reset {tbl}: {e}")
            pg.commit()

        print(
            f"[done] Migrated {total} row-inserts (some may be no-ops without --truncate-fp).",
            flush=True,
        )
    except Exception as e:
        pg.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        sl.close()
        pg.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
