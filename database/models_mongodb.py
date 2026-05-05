"""
MongoDB Models for MarketMinds.ai
MongoDB database models for user management using MongoEngine
"""
from flask_mongoengine import MongoEngine
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

# Initialize MongoEngine (will be initialized in __init__.py)
db = MongoEngine()

class User(UserMixin, db.Document):
    """User model for authentication and profile management"""
    
    # User credentials
    username = db.StringField(max_length=50, required=True, unique=True)
    email = db.StringField(max_length=100, required=True, unique=True)
    password_hash = db.StringField(max_length=255, required=True)
    
    # Profile information
    first_name = db.StringField(max_length=50)
    last_name = db.StringField(max_length=50)
    profile_picture = db.StringField(max_length=255, default='default_avatar.png')
    
    # Account status
    is_active = db.BooleanField(default=True)
    is_premium = db.BooleanField(default=False)
    email_verified = db.BooleanField(default=False)
    
    # Timestamps
    created_at = db.DateTimeField(default=datetime.utcnow)
    updated_at = db.DateTimeField(default=datetime.utcnow)
    last_login = db.DateTimeField()
    
    # Meta information
    meta = {
        'collection': 'UserMangament',  # Collection name as per MongoDB Atlas
        'indexes': [
            'username',
            'email',
            ('username', 'email')  # Compound index
        ]
    }
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
        self.updated_at = datetime.utcnow()
    
    def check_password(self, password):
        """Check if password matches"""
        return check_password_hash(self.password_hash, password)
    
    def update_username(self, new_username):
        """Update username"""
        self.username = new_username
        self.updated_at = datetime.utcnow()
        self.save()
    
    def update_profile_picture(self, filename):
        """Update profile picture filename"""
        # Delete old picture if it's not the default
        if self.profile_picture and self.profile_picture != 'default_avatar.png':
            old_path = os.path.join('static', 'uploads', 'profiles', self.profile_picture)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except:
                    pass
        
        self.profile_picture = filename
        self.updated_at = datetime.utcnow()
        self.save()
    
    def to_dict(self):
        """Convert user to dictionary"""
        return {
            'id': str(self.id),
            'username': self.username,
            'email': self.email,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'profile_picture': self.profile_picture,
            'is_active': self.is_active,
            'is_premium': self.is_premium,
            'email_verified': self.email_verified,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }
    
    def get_id(self):
        """Return the user ID as string (required by Flask-Login)"""
        return str(self.id)
    
    def __repr__(self):
        return f'<User {self.username}>'
