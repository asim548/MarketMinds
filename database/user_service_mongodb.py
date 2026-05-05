"""
User Service for MarketMinds.ai (MongoDB Version)
Handles all user management operations (CRUD) using MongoDB
"""
from database.models_mongodb import User
from werkzeug.security import check_password_hash
from datetime import datetime
from flask import current_app
import os
import uuid
from werkzeug.utils import secure_filename

class UserService:
    """Service class for user management operations with MongoDB"""
    
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    
    @staticmethod
    def allowed_file(filename):
        """Check if file extension is allowed"""
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in UserService.ALLOWED_EXTENSIONS
    
    @staticmethod
    def create_user(username, email, password, first_name=None, last_name=None):
        """
        Create a new user
        
        Args:
            username: Unique username
            email: Unique email address
            password: Plain text password (will be hashed)
            first_name: Optional first name
            last_name: Optional last name
        
        Returns:
            tuple: (User object, error_message)
        """
        # Validate inputs
        if not username or len(username) < 3:
            return None, "Username must be at least 3 characters long"
        
        if not email or '@' not in email:
            return None, "Invalid email address"
        
        if not password or len(password) < 8:
            return None, "Password must be at least 8 characters long"
        
        # Check if username already exists
        if UserService.get_user_by_username(username):
            return None, "Username already exists"
        
        # Check if email already exists
        if UserService.get_user_by_email(email):
            return None, "Email already registered"
        
        try:
            # Create new user
            user = User(
                username=username,
                email=email.lower().strip(),
                first_name=first_name,
                last_name=last_name
            )
            user.set_password(password)
            user.save()
            
            return user, None
        except Exception as e:
            return None, f"Error creating user: {str(e)}"
    
    @staticmethod
    def authenticate_user(username, password):
        """
        Authenticate a user with username and password
        
        Args:
            username: Username or email
            password: Plain text password
        
        Returns:
            tuple: (User object, error_message)
        """
        if not username or not password:
            return None, "Username and password are required"
        
        # Try to find user by username or email
        user = UserService.get_user_by_username(username)
        if not user:
            user = UserService.get_user_by_email(username)
        
        if not user:
            return None, "Invalid username or password"
        
        if not user.is_active:
            return None, "Your account has been deactivated"
        
        if not user.check_password(password):
            return None, "Invalid username or password"
        
        # Update last login
        try:
            user.last_login = datetime.utcnow()
            user.updated_at = datetime.utcnow()
            user.save()
        except:
            pass
        
        return user, None
    
    @staticmethod
    def get_user_by_id(user_id):
        """Get user by ID"""
        try:
            return User.objects(id=user_id).first()
        except:
            return None
    
    @staticmethod
    def get_user_by_username(username):
        """Get user by username"""
        try:
            return User.objects(username=username).first()
        except:
            return None
    
    @staticmethod
    def get_user_by_email(email):
        """Get user by email"""
        try:
            return User.objects(email=email.lower().strip()).first()
        except:
            return None
    
    @staticmethod
    def update_username(user, new_username):
        """
        Update user's username
        
        Args:
            user: User object
            new_username: New username
        
        Returns:
            tuple: (success, error_message)
        """
        if not new_username or len(new_username) < 3:
            return False, "Username must be at least 3 characters long"
        
        # Check if new username is different
        if user.username == new_username:
            return False, "New username must be different from current username"
        
        # Check if username already exists
        existing_user = UserService.get_user_by_username(new_username)
        if existing_user and str(existing_user.id) != str(user.id):
            return False, "Username already taken"
        
        try:
            user.update_username(new_username)
            return True, None
        except Exception as e:
            return False, f"Error updating username: {str(e)}"
    
    @staticmethod
    def update_profile(user, first_name=None, last_name=None, email=None):
        """
        Update user profile information
        
        Args:
            user: User object
            first_name: Optional first name
            last_name: Optional last name
            email: Optional email
        
        Returns:
            tuple: (success, error_message)
        """
        try:
            if first_name is not None:
                user.first_name = first_name
            
            if last_name is not None:
                user.last_name = last_name
            
            if email:
                email = email.lower().strip()
                # Check if email is different and not already taken
                if email != user.email:
                    existing_user = UserService.get_user_by_email(email)
                    if existing_user:
                        return False, "Email already registered"
                    user.email = email
            
            user.updated_at = datetime.utcnow()
            user.save()
            return True, None
        except Exception as e:
            return False, f"Error updating profile: {str(e)}"
    
    @staticmethod
    def change_password(user, old_password, new_password):
        """
        Change user password
        
        Args:
            user: User object
            old_password: Current password
            new_password: New password
        
        Returns:
            tuple: (success, error_message)
        """
        if not user.check_password(old_password):
            return False, "Current password is incorrect"
        
        if not new_password or len(new_password) < 8:
            return False, "New password must be at least 8 characters long"
        
        try:
            user.set_password(new_password)
            return True, None
        except Exception as e:
            return False, f"Error changing password: {str(e)}"
    
    @staticmethod
    def upload_profile_picture(user, file):
        """
        Upload and save profile picture
        
        Args:
            user: User object
            file: File object from request
        
        Returns:
            tuple: (filename, error_message)
        """
        if not file or file.filename == '':
            return None, "No file selected"
        
        if not UserService.allowed_file(file.filename):
            return None, f"File type not allowed. Allowed types: {', '.join(UserService.ALLOWED_EXTENSIONS)}"
        
        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > UserService.MAX_FILE_SIZE:
            return None, f"File size exceeds maximum allowed size of {UserService.MAX_FILE_SIZE / (1024*1024)}MB"
        
        try:
            # Create upload directory if it doesn't exist
            upload_dir = os.path.join('static', 'uploads', 'profiles')
            os.makedirs(upload_dir, exist_ok=True)
            
            # Generate unique filename
            file_ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{user.id}_{uuid.uuid4().hex[:8]}.{file_ext}"
            filepath = os.path.join(upload_dir, filename)
            
            # Save file
            file.save(filepath)
            
            # Update user's profile picture
            user.update_profile_picture(filename)
            
            return filename, None
        except Exception as e:
            return None, f"Error uploading profile picture: {str(e)}"
    
    @staticmethod
    def delete_user(user):
        """
        Delete a user account
        
        Args:
            user: User object
        
        Returns:
            tuple: (success, error_message)
        """
        try:
            # Delete profile picture if exists
            if user.profile_picture and user.profile_picture != 'default_avatar.png':
                picture_path = os.path.join('static', 'uploads', 'profiles', user.profile_picture)
                if os.path.exists(picture_path):
                    try:
                        os.remove(picture_path)
                    except:
                        pass
            
            user.delete()
            return True, None
        except Exception as e:
            return False, f"Error deleting user: {str(e)}"
    
    @staticmethod
    def deactivate_user(user):
        """Deactivate a user account"""
        try:
            user.is_active = False
            user.updated_at = datetime.utcnow()
            user.save()
            return True, None
        except Exception as e:
            return False, f"Error deactivating user: {str(e)}"
    
    @staticmethod
    def activate_user(user):
        """Activate a user account"""
        try:
            user.is_active = True
            user.updated_at = datetime.utcnow()
            user.save()
            return True, None
        except Exception as e:
            return False, f"Error activating user: {str(e)}"
