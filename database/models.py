"""
SQLAlchemy models for MarketMinds.ai user management.
"""

import os
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from database import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    profile_picture = db.Column(db.String(255), default="default_avatar.png")

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_premium = db.Column(db.Boolean, default=False, nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    google_sub = db.Column(db.String(255), nullable=True, unique=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.updated_at = datetime.utcnow()

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def update_username(self, new_username):
        self.username = new_username
        self.updated_at = datetime.utcnow()

    def update_profile_picture(self, filename):
        if self.profile_picture and self.profile_picture != "default_avatar.png":
            old_path = os.path.join("static", "uploads", "profiles", self.profile_picture)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        self.profile_picture = filename
        self.updated_at = datetime.utcnow()

    def to_dict(self):
        return {
            "id": str(self.id),
            "username": self.username,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "profile_picture": self.profile_picture,
            "is_active": self.is_active,
            "is_premium": self.is_premium,
            "email_verified": self.email_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    def get_id(self):
        return str(self.id)

    def __repr__(self):
        return f"<User {self.username}>"

