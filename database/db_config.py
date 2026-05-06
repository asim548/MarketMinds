"""
SQLite configuration for MarketMinds.ai.
"""

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, inspect, text

from database import db


class DatabaseConfig:
    """SQLAlchemy database configuration class."""

    BASE_DIR = Path(__file__).resolve().parent.parent
    DEFAULT_DB_PATH = BASE_DIR / "marketminds.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @staticmethod
    def _sqlite_url() -> str:
        return f"sqlite:///{DatabaseConfig.DEFAULT_DB_PATH}"

    @staticmethod
    def _with_connect_timeout(url: str, seconds: int = 5) -> str:
        """Ensure Postgres URLs fail fast instead of stalling startup."""
        try:
            parts = urlsplit(url)
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            if "connect_timeout" not in q:
                q["connect_timeout"] = str(seconds)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
        except Exception:
            return url

    @staticmethod
    def _postgres_reachable(url: str) -> bool:
        """Best-effort connectivity probe; fallback to SQLite when unreachable."""
        try:
            engine = create_engine(url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            return True
        except Exception as e:
            print(f"[DB] PostgreSQL preflight failed: {e}")
            return False

    @staticmethod
    def _resolved_database_url() -> str:
        """
        Prefer MARKETMINDS_DATABASE_URL if set (avoids Railway Postgres plugin
        injecting DATABASE_URL when this app expects SQLite).

        If DATABASE_URL is Postgres but no driver is installed, fall back to SQLite
        so the process can boot and pass healthchecks.
        """
        explicit = (os.environ.get("MARKETMINDS_DATABASE_URL") or "").strip()
        if explicit:
            url = explicit
        else:
            url = (os.environ.get("DATABASE_URL") or "").strip()
            if not url:
                return DatabaseConfig._sqlite_url()

        if url.startswith(("postgres://", "postgresql://")):
            try:
                import psycopg  # noqa: F401
            except ImportError:
                try:
                    import psycopg2  # noqa: F401
                except ImportError:
                    print(
                        "[DB] DATABASE_URL is PostgreSQL but no psycopg/psycopg2 driver found; "
                        "using local SQLite instead. Set MARKETMINDS_DATABASE_URL or install psycopg2-binary."
                    )
                    return DatabaseConfig._sqlite_url()

            url = DatabaseConfig._with_connect_timeout(url, seconds=5)
            if not DatabaseConfig._postgres_reachable(url):
                print("[DB] Falling back to SQLite because PostgreSQL is unreachable at boot.")
                return DatabaseConfig._sqlite_url()

        return url

    @classmethod
    def get_config(cls):
        return {
            "SQLALCHEMY_DATABASE_URI": cls._resolved_database_url(),
            "SQLALCHEMY_TRACK_MODIFICATIONS": cls.SQLALCHEMY_TRACK_MODIFICATIONS,
        }

    @classmethod
    def init_app(cls, app):
        config = cls.get_config()
        for key, value in config.items():
            app.config[key] = value

        db.init_app(app)
        with app.app_context():
            db.create_all()
            cls._ensure_sqlite_google_sub_column()
        print(f"[OK] Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")

    @staticmethod
    def _ensure_sqlite_google_sub_column() -> None:
        """Existing SQLite DBs: add users.google_sub (create_all does not ALTER tables)."""
        try:
            if db.engine.dialect.name != "sqlite":
                return
            insp = inspect(db.engine)
            if "users" not in insp.get_table_names():
                return
            cols = {c["name"] for c in insp.get_columns("users")}
            if "google_sub" in cols:
                return
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(255)"))
            print("[DB] Added column users.google_sub for Google sign-in.")
        except Exception as e:
            print(f"[DB] google_sub migration skipped: {e}")

