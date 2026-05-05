"""
MongoDB Initialization Script for MarketMinds.ai
Creates database collections and optionally creates admin user
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import User
from database.db_config import DatabaseConfig
from flask import Flask

def create_app():
    """Create Flask app for database initialization"""
    app = Flask(__name__)
    DatabaseConfig.init_app(app)
    # MongoDB connection is handled in DatabaseConfig.init_app()
    
    return app

def init_database():
    """Initialize database - create indexes and admin user"""
    app = create_app()
    
    with app.app_context():
        # Create indexes (MongoDB creates collections automatically on first insert)
        try:
            User.ensure_indexes()
            print("[OK] Database indexes created successfully!")
        except Exception as e:
            print(f"[WARNING] Could not create indexes: {e}")
        
        # Check if admin user exists
        admin = User.objects(username='admin').first()
        if not admin:
            # Create admin user
            admin = User(
                username='admin',
                email='admin@marketminds.ai',
                is_premium=True,
                is_active=True
            )
            admin.set_password('admin123')  # Change this in production!
            admin.save()
            print("[OK] Admin user created!")
            print("  Username: admin")
            print("  Password: admin123")
            print("  [WARNING] Please change the admin password after first login!")
        else:
            print("[OK] Admin user already exists")
        
        # Count users
        user_count = User.objects.count()
        print(f"[OK] Total users in database: {user_count}")
        
        return True

if __name__ == '__main__':
    print("Initializing MarketMinds.ai MongoDB database...")
    print("Database: MarketMinds")
    print("Collection: UserMangament")
    print("-" * 50)
    
    try:
        init_database()
        print("-" * 50)
        print("[OK] Database initialization completed successfully!")
    except Exception as e:
        print(f"[ERROR] Error initializing database: {str(e)}")
        import traceback
        traceback.print_exc()
