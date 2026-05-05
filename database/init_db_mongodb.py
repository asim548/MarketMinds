"""
MongoDB Initialization Script for MarketMinds.ai
Creates database collections and optionally creates admin user
"""
from database.models_mongodb import User
from database.db_config_mongodb import DatabaseConfig
from flask import Flask

def create_app():
    """Create Flask app for database initialization"""
    app = Flask(__name__)
    DatabaseConfig.init_app(app)
    
    # Initialize MongoEngine
    from database import db
    db.init_app(app)
    
    return app

def init_database():
    """Initialize database - create indexes and admin user"""
    app = create_app()
    
    with app.app_context():
        # Create indexes (MongoDB creates collections automatically on first insert)
        try:
            User.ensure_indexes()
            print("✓ Database indexes created successfully!")
        except Exception as e:
            print(f"⚠️  Warning: Could not create indexes: {e}")
        
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
            print("✓ Admin user created!")
            print("  Username: admin")
            print("  Password: admin123")
            print("  ⚠️  Please change the admin password after first login!")
        else:
            print("✓ Admin user already exists")
        
        # Count users
        user_count = User.objects.count()
        print(f"✓ Total users in database: {user_count}")
        
        return True

if __name__ == '__main__':
    print("Initializing MarketMinds.ai MongoDB database...")
    print("-" * 50)
    
    try:
        init_database()
        print("-" * 50)
        print("✓ Database initialization completed successfully!")
    except Exception as e:
        print(f"✗ Error initializing database: {str(e)}")
        import traceback
        traceback.print_exc()
