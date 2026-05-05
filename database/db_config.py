"""
SQLite configuration for MarketMinds.ai.
"""

import os
from pathlib import Path

from sqlalchemy import inspect, text

from database import db


class DatabaseConfig:
    """SQLAlchemy database configuration class."""

    BASE_DIR = Path(__file__).resolve().parent.parent
    DEFAULT_DB_PATH = BASE_DIR / "marketminds.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
            return explicit

        url = (os.environ.get("DATABASE_URL") or "").strip()
        if not url:
            return f"sqlite:///{DatabaseConfig.DEFAULT_DB_PATH}"

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
                    return f"sqlite:///{DatabaseConfig.DEFAULT_DB_PATH}"

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

