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

    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @classmethod
    def get_config(cls):
        return {
            "SQLALCHEMY_DATABASE_URI": cls.DATABASE_URL,
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
        print(f"[OK] Connected to SQLite: {cls.DATABASE_URL}")

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

