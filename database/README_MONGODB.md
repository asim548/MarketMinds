# Database Package - MongoDB Version

This folder contains MongoDB-compatible database code for MarketMinds.ai user management system.

## 📁 File Structure

```
database/
├── models_mongodb.py          # MongoDB User model (MongoEngine)
├── db_config_mongodb.py       # MongoDB configuration
├── user_service_mongodb.py     # User management service (MongoDB)
├── __init__mongodb__.py        # Package initialization (MongoDB)
├── init_db_mongodb.py          # Database initialization (MongoDB)
└── README_MONGODB.md          # This file
```

## 🔧 MongoDB vs PostgreSQL Files

| Purpose | PostgreSQL | MongoDB |
|---------|-----------|---------|
| Models | `models.py` | `models_mongodb.py` |
| Config | `db_config.py` | `db_config_mongodb.py` |
| Service | `user_service.py` | `user_service_mongodb.py` |
| Init | `__init__.py` | `__init__mongodb__.py` |
| Setup | `init_db.py` | `init_db_mongodb.py` |

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install Flask-MongoEngine pymongo python-dotenv --user
```

### 2. Configure MongoDB
Create `.env`:
```env
DATABASE_URL=mongodb://localhost:27017/marketminds
```

### 3. Initialize Database
```bash
python database/init_db_mongodb.py
```

### 4. Use in Your App
```python
from database.models_mongodb import User
from database.db_config_mongodb import DatabaseConfig
from database.user_service_mongodb import UserService

DatabaseConfig.init_app(app)
db.init_app(app)
```

## 📝 Key Differences

### Models
- **PostgreSQL**: `db.Model` with `db.Column`
- **MongoDB**: `db.Document` with `db.StringField`, `db.DateTimeField`, etc.

### Queries
- **PostgreSQL**: `User.query.filter_by(username='john').first()`
- **MongoDB**: `User.objects(username='john').first()`

### Saving
- **PostgreSQL**: `db.session.add(user); db.session.commit()`
- **MongoDB**: `user.save()`

### IDs
- **PostgreSQL**: Integer IDs (`user.id`)
- **MongoDB**: ObjectId (`str(user.id)`)

## 🔐 User Service Methods

All methods work the same way:

```python
from database.user_service_mongodb import UserService

# Create user
user, error = UserService.create_user('username', 'email@example.com', 'password')

# Authenticate
user, error = UserService.authenticate_user('username', 'password')

# Update username
success, error = UserService.update_username(user, 'new_username')

# Upload picture
filename, error = UserService.upload_profile_picture(user, file)
```

## 📊 MongoDB Document Structure

```json
{
  "_id": ObjectId("..."),
  "username": "johndoe",
  "email": "john@example.com",
  "password_hash": "$2b$12$...",
  "first_name": "John",
  "last_name": "Doe",
  "profile_picture": "default_avatar.png",
  "is_active": true,
  "is_premium": false,
  "email_verified": false,
  "created_at": ISODate("2024-01-01T00:00:00Z"),
  "updated_at": ISODate("2024-01-01T00:00:00Z"),
  "last_login": ISODate("2024-01-15T10:30:00Z")
}
```

## 🔒 Security Features

- ✅ Password hashing with Werkzeug (bcrypt)
- ✅ Input validation on all operations
- ✅ File type and size validation
- ✅ Unique constraints on username and email
- ✅ Indexes for performance

## 📚 Documentation

- MongoDB Docs: https://docs.mongodb.com/

## ⚠️ Notes

1. MongoDB creates collections automatically on first insert
2. No migrations needed - schema is flexible
3. Use `.objects()` for queries, not `.query`
4. IDs are ObjectId, convert to string for Flask-Login
5. Use `.save()` instead of session commits

---

**MongoDB version ready to use! 🎉**
