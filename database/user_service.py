"""
User Service for MarketMinds.ai (SQLite + SQLAlchemy).
"""

import os
import re
import secrets
import uuid
from datetime import datetime

from database import db
from database.models import User


class UserService:
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
    MAX_FILE_SIZE = 5 * 1024 * 1024

    @staticmethod
    def allowed_file(filename):
        return "." in filename and filename.rsplit(".", 1)[1].lower() in UserService.ALLOWED_EXTENSIONS

    @staticmethod
    def create_user(username, email, password, first_name=None, last_name=None):
        if not username or len(username) < 3:
            return None, "Username must be at least 3 characters long"
        if not email or "@" not in email:
            return None, "Invalid email address"
        if not password or len(password) < 8:
            return None, "Password must be at least 8 characters long"

        if UserService.get_user_by_username(username):
            return None, "Username already exists"
        if UserService.get_user_by_email(email):
            return None, "Email already registered"

        try:
            user = User(
                username=username.strip(),
                email=email.lower().strip(),
                first_name=first_name,
                last_name=last_name,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            return user, None
        except Exception as e:
            db.session.rollback()
            return None, f"Error creating user: {str(e)}"

    @staticmethod
    def authenticate_user(username, password):
        if not username or not password:
            return None, "Username and password are required"

        user = UserService.get_user_by_username(username) or UserService.get_user_by_email(username)
        if not user:
            return None, "Invalid username or password"
        if not user.is_active:
            return None, "Your account has been deactivated"
        if not user.check_password(password):
            return None, "Invalid username or password"

        try:
            user.last_login = datetime.utcnow()
            user.updated_at = datetime.utcnow()
            db.session.commit()
        except Exception:
            db.session.rollback()

        return user, None

    @staticmethod
    def get_user_by_id(user_id):
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    @staticmethod
    def get_user_by_username(username):
        try:
            return User.query.filter_by(username=username).first()
        except Exception:
            return None

    @staticmethod
    def get_user_by_email(email):
        try:
            return User.query.filter_by(email=email.lower().strip()).first()
        except Exception:
            return None

    @staticmethod
    def get_user_by_google_sub(google_sub: str):
        if not google_sub:
            return None
        try:
            return User.query.filter_by(google_sub=google_sub).first()
        except Exception:
            return None

    @staticmethod
    def get_or_create_from_google(profile: dict):
        """
        profile: Google userinfo (sub, email, given_name, family_name, email_verified, ...).
        Links google_sub to an existing user with the same email when possible.
        """
        google_sub = (profile.get("sub") or "").strip()
        email = (profile.get("email") or "").lower().strip()
        if not google_sub or not email:
            return None, "Google did not return email or account id."

        existing = UserService.get_user_by_google_sub(google_sub)
        if existing:
            try:
                existing.last_login = datetime.utcnow()
                existing.updated_at = datetime.utcnow()
                if profile.get("email_verified"):
                    existing.email_verified = True
                db.session.commit()
            except Exception:
                db.session.rollback()
            return existing, None

        by_email = UserService.get_user_by_email(email)
        if by_email:
            try:
                by_email.google_sub = google_sub
                by_email.last_login = datetime.utcnow()
                by_email.updated_at = datetime.utcnow()
                if profile.get("email_verified"):
                    by_email.email_verified = True
                gn = profile.get("given_name")
                fn = profile.get("family_name")
                if gn and not by_email.first_name:
                    by_email.first_name = gn[:50]
                if fn and not by_email.last_name:
                    by_email.last_name = fn[:50]
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                return None, str(e)
            return by_email, None

        local = email.split("@", 1)[0]
        local = re.sub(r"[^a-zA-Z0-9_]", "_", local).strip("_") or "trader"
        local = local[:20]
        username = local
        n = 0
        while UserService.get_user_by_username(username):
            n += 1
            suffix = str(n)
            username = (local[: max(1, 50 - len(suffix))] + suffix)[:50]

        first = (profile.get("given_name") or "")[:50] or None
        last = (profile.get("family_name") or "")[:50] or None
        dummy_pw = secrets.token_urlsafe(32)
        user = User(
            username=username,
            email=email,
            first_name=first,
            last_name=last,
            google_sub=google_sub,
            email_verified=bool(profile.get("email_verified")),
        )
        user.set_password(dummy_pw)
        try:
            db.session.add(user)
            db.session.commit()
            return user, None
        except Exception as e:
            db.session.rollback()
            return None, f"Could not create account: {e}"

    @staticmethod
    def update_username(user, new_username):
        if not new_username or len(new_username) < 3:
            return False, "Username must be at least 3 characters long"
        if user.username == new_username:
            return False, "New username must be different from current username"

        existing_user = UserService.get_user_by_username(new_username)
        if existing_user and existing_user.id != user.id:
            return False, "Username already taken"

        try:
            user.update_username(new_username)
            db.session.commit()
            return True, None
        except Exception as e:
            db.session.rollback()
            return False, f"Error updating username: {str(e)}"

    @staticmethod
    def update_profile(user, first_name=None, last_name=None, email=None):
        try:
            if first_name is not None:
                user.first_name = first_name
            if last_name is not None:
                user.last_name = last_name
            if email:
                email = email.lower().strip()
                if email != user.email:
                    existing_user = UserService.get_user_by_email(email)
                    if existing_user:
                        return False, "Email already registered"
                    user.email = email

            user.updated_at = datetime.utcnow()
            db.session.commit()
            return True, None
        except Exception as e:
            db.session.rollback()
            return False, f"Error updating profile: {str(e)}"

    @staticmethod
    def change_password(user, old_password, new_password):
        if not user.check_password(old_password):
            return False, "Current password is incorrect"
        if not new_password or len(new_password) < 8:
            return False, "New password must be at least 8 characters long"

        try:
            user.set_password(new_password)
            db.session.commit()
            return True, None
        except Exception as e:
            db.session.rollback()
            return False, f"Error changing password: {str(e)}"

    @staticmethod
    def upload_profile_picture(user, file):
        if not file or file.filename == "":
            return None, "No file selected"
        if not UserService.allowed_file(file.filename):
            return None, f"File type not allowed. Allowed types: {', '.join(UserService.ALLOWED_EXTENSIONS)}"

        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > UserService.MAX_FILE_SIZE:
            return None, f"File size exceeds maximum allowed size of {UserService.MAX_FILE_SIZE / (1024*1024)}MB"

        try:
            upload_dir = os.path.join("static", "uploads", "profiles")
            os.makedirs(upload_dir, exist_ok=True)

            file_ext = file.filename.rsplit(".", 1)[1].lower()
            filename = f"{user.id}_{uuid.uuid4().hex[:8]}.{file_ext}"
            filepath = os.path.join(upload_dir, filename)
            file.save(filepath)

            user.update_profile_picture(filename)
            db.session.commit()
            return filename, None
        except Exception as e:
            db.session.rollback()
            return None, f"Error uploading profile picture: {str(e)}"

    @staticmethod
    def delete_user(user):
        try:
            if user.profile_picture and user.profile_picture != "default_avatar.png":
                picture_path = os.path.join("static", "uploads", "profiles", user.profile_picture)
                if os.path.exists(picture_path):
                    try:
                        os.remove(picture_path)
                    except Exception:
                        pass

            db.session.delete(user)
            db.session.commit()
            return True, None
        except Exception as e:
            db.session.rollback()
            return False, f"Error deleting user: {str(e)}"

    @staticmethod
    def deactivate_user(user):
        try:
            user.is_active = False
            user.updated_at = datetime.utcnow()
            db.session.commit()
            return True, None
        except Exception as e:
            db.session.rollback()
            return False, f"Error deactivating user: {str(e)}"

    @staticmethod
    def activate_user(user):
        try:
            user.is_active = True
            user.updated_at = datetime.utcnow()
            db.session.commit()
            return True, None
        except Exception as e:
            db.session.rollback()
            return False, f"Error activating user: {str(e)}"

