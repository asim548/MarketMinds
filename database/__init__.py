"""
Database package for MarketMinds.ai (SQLite + SQLAlchemy).
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from database.models import User  # noqa: E402

__all__ = ["db", "User"]

