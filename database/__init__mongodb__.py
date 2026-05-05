"""
Database Package for MarketMinds.ai (MongoDB Version)
Initializes MongoDB database and exports main components
"""
from flask_mongoengine import MongoEngine

# Initialize MongoEngine instance
db = MongoEngine()

# Import models after db initialization to avoid circular imports
from database.models_mongodb import User

# Export main components
__all__ = ['db', 'User']
