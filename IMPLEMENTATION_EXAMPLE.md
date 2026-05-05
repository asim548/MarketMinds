# Database Implementation Example

This file shows you exactly how to implement the database system step by step.

## Step 1: Install Required Packages

```bash
pip install Flask-SQLAlchemy Flask-Migrate Flask-Login Flask-WTF Flask-Mail Werkzeug --user
```

## Step 2: Create Database Models (models.py)

```python
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User model for authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    is_premium = db.Column(db.Boolean, default=False)
    email_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    profile_picture = db.Column(db.String(255))
    
    # Relationships
    preferences = db.relationship('UserPreferences', backref='user', uselist=False, cascade='all, delete-orphan')
    watchlists = db.relationship('Watchlist', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    trading_history = db.relationship('TradingHistory', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if password matches"""
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'


class UserPreferences(db.Model):
    """User preferences and settings"""
    __tablename__ = 'user_preferences'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    theme = db.Column(db.String(20), default='dark')
    currency = db.Column(db.String(10), default='USD')
    timezone = db.Column(db.String(50), default='UTC')
    notifications_enabled = db.Column(db.Boolean, default=True)
    email_notifications = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Watchlist(db.Model):
    """User watchlists"""
    __tablename__ = 'watchlists'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    items = db.relationship('WatchlistItem', backref='watchlist', lazy='dynamic', cascade='all, delete-orphan')


class WatchlistItem(db.Model):
    """Items in a watchlist"""
    __tablename__ = 'watchlist_items'
    
    id = db.Column(db.Integer, primary_key=True)
    watchlist_id = db.Column(db.Integer, db.ForeignKey('watchlists.id'), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    symbol_type = db.Column(db.String(20), nullable=False)  # 'Stock', 'Crypto', 'Forex', 'Commodity'
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('watchlist_id', 'symbol', 'symbol_type', name='unique_watchlist_item'),)


class TradingHistory(db.Model):
    """User trading history"""
    __tablename__ = 'trading_history'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    symbol = db.Column(db.String(20), nullable=False)
    symbol_type = db.Column(db.String(20), nullable=False)
    action = db.Column(db.String(10), nullable=False)  # 'BUY', 'SELL', 'HOLD'
    entry_price = db.Column(db.Numeric(15, 4))
    exit_price = db.Column(db.Numeric(15, 4))
    quantity = db.Column(db.Numeric(15, 4))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserActivityLog(db.Model):
    """User activity audit log"""
    __tablename__ = 'user_activity_log'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
```

## Step 3: Update app.py - Database Configuration

Add to the top of `app.py`:

```python
from models import db, User, UserPreferences, Watchlist, WatchlistItem, TradingHistory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate

# Initialize extensions
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
migrate = Migrate()

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///marketminds.db'  # For SQLite
# For PostgreSQL: 'postgresql://username:password@localhost/marketminds'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
login_manager.init_app(app)
migrate.init_app(app, db)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
```

## Step 4: Update Login Route

Replace the existing login route with:

```python
from flask import flash
from werkzeug.security import check_password_hash

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            return render_template('login.html', error='Please fill in all fields')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                return render_template('login.html', error='Your account has been deactivated')
            
            login_user(user, remember=request.form.get('remember_me'))
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            # Log activity
            log_activity(user.id, 'LOGIN', f'User {username} logged in', request.remote_addr)
            
            flash(f'Welcome back, {user.username}!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')
```

## Step 5: Update Signup Route

Replace the existing signup route with:

```python
from werkzeug.security import generate_password_hash
from flask import flash

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        errors = []
        
        if not username or len(username) < 3:
            errors.append('Username must be at least 3 characters')
        
        if User.query.filter_by(username=username).first():
            errors.append('Username already exists')
        
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered')
        
        if not email or '@' not in email:
            errors.append('Invalid email address')
        
        if not password or len(password) < 8:
            errors.append('Password must be at least 8 characters')
        
        if password != confirm_password:
            errors.append('Passwords do not match')
        
        if errors:
            return render_template('signup.html', errors=errors)
        
        # Create user
        user = User(
            username=username,
            email=email
        )
        user.set_password(password)
        
        try:
            db.session.add(user)
            db.session.commit()
            
            # Create default preferences
            preferences = UserPreferences(user_id=user.id)
            db.session.add(preferences)
            db.session.commit()
            
            # Log activity
            log_activity(user.id, 'SIGNUP', f'New user {username} registered', request.remote_addr)
            
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            return render_template('signup.html', error=f'Error creating account: {str(e)}')
    
    return render_template('signup.html')
```

## Step 6: Update Logout Route

```python
@app.route('/logout')
@login_required
def logout():
    username = current_user.username
    log_activity(current_user.id, 'LOGOUT', f'User {username} logged out', request.remote_addr)
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))
```

## Step 7: Add Helper Function for Activity Logging

```python
def log_activity(user_id, activity_type, description, ip_address=None):
    """Log user activity"""
    try:
        activity = UserActivityLog(
            user_id=user_id,
            activity_type=activity_type,
            description=description,
            ip_address=ip_address or request.remote_addr
        )
        db.session.add(activity)
        db.session.commit()
    except Exception as e:
        print(f"Error logging activity: {e}")
```

## Step 8: Update Dashboard Route

```python
@app.route('/dashboard')
@login_required
def dashboard():
    # Get user's watchlists
    watchlists = Watchlist.query.filter_by(user_id=current_user.id).all()
    
    # Get recent trading history
    recent_trades = TradingHistory.query.filter_by(user_id=current_user.id)\
        .order_by(TradingHistory.created_at.desc()).limit(10).all()
    
    # Fetch market data (your existing code)
    try:
        crypto_data = data_fetcher.get_crypto_data()
        stocks_data = data_fetcher.get_stocks_data()
        forex_data = data_fetcher.get_forex_data()
    except Exception as e:
        print(f"Error fetching market data: {e}")
        crypto_data = []
        stocks_data = []
        forex_data = []
    
    return render_template('dashboard.html',
                         crypto_data=crypto_data,
                         stocks_data=stocks_data,
                         forex_data=forex_data,
                         username=current_user.username,
                         watchlists=watchlists,
                         recent_trades=recent_trades)
```

## Step 9: Initialize Database

Create a file `init_db.py`:

```python
from app import app
from models import db, User, UserPreferences

with app.app_context():
    # Create all tables
    db.create_all()
    
    # Create admin user (optional)
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@marketminds.ai',
            is_premium=True
        )
        admin.set_password('admin123')  # Change this!
        db.session.add(admin)
        
        # Create preferences
        prefs = UserPreferences(user_id=admin.id)
        db.session.add(prefs)
        db.session.commit()
        print("Admin user created!")
    
    print("Database initialized!")
```

Run: `python init_db.py`

## Step 10: Update base.html

Replace `current_user` references with Flask-Login's `current_user`:

```jinja2
{% if current_user.is_authenticated %}
    <a href="{{ url_for('dashboard') }}" class="nav-user">
        <i class="fas fa-user-circle"></i>
        {{ current_user.username }}
    </a>
    <a href="{{ url_for('logout') }}" class="nav-link logout">
        <i class="fas fa-sign-out-alt"></i>
        Logout
    </a>
{% else %}
    <!-- Login/Signup links -->
{% endif %}
```

## Step 11: Protect Routes

Add `@login_required` decorator to protected routes:

```python
@app.route('/ai_picks')
@login_required
def ai_picks():
    # Your existing code
    pass

@app.route('/chart_ai')
@login_required
def chart_ai():
    # Your existing code
    pass
```

## Step 12: Create User Profile Page

```python
@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        current_user.first_name = request.form.get('first_name', '')
        current_user.last_name = request.form.get('last_name', '')
        current_user.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
    return render_template('edit_profile.html', user=current_user)
```

---

## Quick Start Commands

```bash
# 1. Install packages
pip install Flask-SQLAlchemy Flask-Migrate Flask-Login Flask-WTF Werkzeug --user

# 2. Initialize database
python init_db.py

# 3. Run migrations (if using Flask-Migrate)
flask db init
flask db migrate -m "Initial migration"
flask db upgrade

# 4. Run app
python app.py
```

---

## Testing

Test the implementation:

1. Go to `/signup` and create an account
2. Login with your credentials
3. Check database: `sqlite3 marketminds.db` then `.tables`
4. View users: `SELECT * FROM users;`

---

## Next Steps

1. Add email verification
2. Add password reset functionality
3. Create watchlist management
4. Add user preferences page
5. Implement trading history tracking
