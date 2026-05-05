"""
MongoDB Configuration for MarketMinds.ai
Handles MongoDB database connection configuration
"""
import os
from dotenv import load_dotenv

load_dotenv()

class DatabaseConfig:
    """MongoDB database configuration class"""
    
    # MongoDB connection string format:
    # mongodb://username:password@host:port/database_name
    # or
    # mongodb://host:port/database_name
    
    # Get database URL from environment variable or use default
    DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('MONGODB_URL')
    
    # If DATABASE_URL is not set, construct it from individual components
    if not DATABASE_URL:
        DB_USER = os.environ.get('DB_USER', 'i222679')
        DB_PASSWORD = os.environ.get('DB_PASSWORD', 'asim1666')
        DB_HOST = os.environ.get('DB_HOST', 'cluster0.zcf6a.mongodb.net')
        DB_NAME = os.environ.get('DB_NAME', 'MarketMinds')
        
        # Build MongoDB Atlas connection string (mongodb+srv)
        if DB_USER and DB_PASSWORD:
            DATABASE_URL = f'mongodb+srv://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}?retryWrites=true&w=majority&appName=Cluster0'
        else:
            DATABASE_URL = f'mongodb+srv://{DB_HOST}/{DB_NAME}?retryWrites=true&w=majority'
    
    # MongoEngine configuration
    MONGODB_SETTINGS = {
        'host': DATABASE_URL,
        'db': os.environ.get('DB_NAME', 'MarketMinds'),
        'connect': False,  # Lazy connection
        'retryWrites': True,
        'w': 'majority'
    }
    
    # Additional MongoDB settings
    MONGODB_DB = os.environ.get('DB_NAME', 'MarketMinds')
    MONGODB_HOST = DB_HOST if not DATABASE_URL else None
    MONGODB_PORT = int(DB_PORT) if not DATABASE_URL else None
    MONGODB_USERNAME = DB_USER if DB_USER else None
    MONGODB_PASSWORD = DB_PASSWORD if DB_PASSWORD else None
    
    @classmethod
    def get_config(cls):
        """Get database configuration dictionary"""
        config = {
            'MONGODB_SETTINGS': cls.MONGODB_SETTINGS
        }
        
        # Add individual settings if not using full URL
        if cls.MONGODB_HOST:
            config['MONGODB_HOST'] = cls.MONGODB_HOST
        if cls.MONGODB_PORT:
            config['MONGODB_PORT'] = cls.MONGODB_PORT
        if cls.MONGODB_USERNAME:
            config['MONGODB_USERNAME'] = cls.MONGODB_USERNAME
        if cls.MONGODB_PASSWORD:
            config['MONGODB_PASSWORD'] = cls.MONGODB_PASSWORD
        
        return config
    
    @classmethod
    def init_app(cls, app):
        """Initialize Flask app with MongoDB configuration"""
        config = cls.get_config()
        for key, value in config.items():
            app.config[key] = value
