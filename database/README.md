# Database Package - User Management System

This folder contains all database-related code for the MarketMinds.ai user management system.

## 📁 Folder Structure

```
database/
├── __init__.py          # Package initialization, exports db and User
├── models.py            # Database models (User model)
├── db_config.py         # Database configuration (PostgreSQL settings)
├── user_service.py      # User management service (CRUD operations)
├── init_db.py           # Database initialization script
└── README.md            # This file
```

## 📋 Files Description

### `__init__.py`
- Initializes SQLAlchemy database instance
- Exports `db` and `User` for easy imports

### `models.py`
- Contains the `User` model class
- Defines database schema for users table
- Includes methods for password hashing, username updates, profile picture management

### `db_config.py`
- Handles PostgreSQL database configuration
- Reads database connection from environment variables
- Provides `DatabaseConfig` class for Flask app initialization

### `user_service.py`
- **UserService** class with all user management operations:
  - `create_user()` - Register new users
  - `authenticate_user()` - Login authentication
  - `get_user_by_id()` - Get user by ID
  - `get_user_by_username()` - Get user by username
  - `get_user_by_email()` - Get user by email
  - `update_username()` - Change username
  - `update_profile()` - Update profile information
  - `change_password()` - Change password
  - `upload_profile_picture()` - Upload profile picture
  - `delete_user()` - Delete user account
  - `deactivate_user()` - Deactivate account
  - `activate_user()` - Activate account

### `init_db.py`
- Database initialization script
- Creates all database tables
- Creates default admin user (if doesn't exist)

## 🚀 Usage

### 1. Configure Database Connection

Create a `.env` file in the project root:

```env
# PostgreSQL Database Configuration
DATABASE_URL=postgresql://username:password@localhost:5432/marketminds

# OR use individual components:
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=marketminds
```

### 2. Initialize Database

```bash
python database/init_db.py
```

### 3. Import in Your Flask App

```python
from database import db, User
from database.db_config import DatabaseConfig
from database.user_service import UserService

# Initialize database
DatabaseConfig.init_app(app)
db.init_app(app)

# Use UserService for operations
user, error = UserService.create_user('john', 'john@example.com', 'password123')
```

## 📝 Example Usage

### Creating a User
```python
from database.user_service import UserService

user, error = UserService.create_user(
    username='johndoe',
    email='john@example.com',
    password='securepassword123',
    first_name='John',
    last_name='Doe'
)

if error:
    print(f"Error: {error}")
else:
    print(f"User created: {user.username}")
```

### Authenticating a User
```python
user, error = UserService.authenticate_user('johndoe', 'securepassword123')

if error:
    print(f"Error: {error}")
else:
    print(f"User authenticated: {user.username}")
```

### Updating Username
```python
user = UserService.get_user_by_username('johndoe')
success, error = UserService.update_username(user, 'newusername')

if error:
    print(f"Error: {error}")
else:
    print("Username updated successfully!")
```

### Uploading Profile Picture
```python
from flask import request

user = UserService.get_user_by_username('johndoe')
file = request.files['profile_picture']
filename, error = UserService.upload_profile_picture(user, file)

if error:
    print(f"Error: {error}")
else:
    print(f"Profile picture uploaded: {filename}")
```

## 🔒 Security Features

- ✅ Password hashing using Werkzeug (bcrypt)
- ✅ Input validation on all operations
- ✅ File type and size validation for profile pictures
- ✅ Unique constraints on username and email
- ✅ Account activation/deactivation support

## 📦 Dependencies

Required packages:
- `Flask-SQLAlchemy` - ORM for database
- `Flask-Login` - User session management
- `Werkzeug` - Password hashing
- `python-dotenv` - Environment variable management
- `psycopg2` or `psycopg2-binary` - PostgreSQL adapter

## 🗄️ Database Schema

### Users Table
- `id` - Primary key
- `username` - Unique username (50 chars)
- `email` - Unique email (100 chars)
- `password_hash` - Hashed password (255 chars)
- `first_name` - First name (50 chars)
- `last_name` - Last name (50 chars)
- `profile_picture` - Profile picture filename (255 chars)
- `is_active` - Account active status (boolean)
- `is_premium` - Premium user status (boolean)
- `email_verified` - Email verification status (boolean)
- `created_at` - Account creation timestamp
- `updated_at` - Last update timestamp
- `last_login` - Last login timestamp

## 🔧 Troubleshooting

### Database Connection Error
- Check PostgreSQL is running
- Verify database credentials in `.env`
- Ensure database exists: `CREATE DATABASE marketminds;`

### Import Errors
- Make sure you're importing from `database` package, not `models`
- Check that `database/__init__.py` exists

### Migration Issues
- Use Flask-Migrate for database migrations
- Run `flask db init` then `flask db migrate` and `flask db upgrade`
