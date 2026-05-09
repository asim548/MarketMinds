import os

# Ultralytics writes under YOLO_CONFIG_DIR; ensure it exists (avoids nested /Ultralytics permission warnings).
_yolo_cfg = (os.environ.get("YOLO_CONFIG_DIR") or "").strip()
if not _yolo_cfg:
    _tw = os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp"
    _yolo_cfg = os.path.join(_tw, "mm-ultralytics")
os.environ["YOLO_CONFIG_DIR"] = _yolo_cfg
try:
    os.makedirs(os.environ["YOLO_CONFIG_DIR"], exist_ok=True)
except OSError:
    pass

# Render (and other hosts using Socket.IO over WebSockets): Werkzeug cannot upgrade
# WebSocket connections — use gevent in production.
if os.environ.get("RENDER"):
    from gevent import monkey

    # Keep native threading for our long-lived background jobs (FP integration/scheduler).
    # Full thread patching can cause KeyError in threading internals under gevent workers.
    monkey.patch_all(thread=False)

from flask import (
    Flask,
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
)
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import json
import plotly
import plotly.graph_objects as go

import threading
import time
import shutil
import urllib.request
from werkzeug.utils import secure_filename
import importlib
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# Database imports (SQLite)
from database import db, User
from database.db_config import DatabaseConfig
from database.user_service import UserService
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

# Flask-SocketIO — needed to serve FinancialPulse WebSockets
from flask_socketio import SocketIO

# SMC Analyzer - optional
try:
    from smc_analyzer import SMCChartAnalyzer
except ImportError:
    class SMCChartAnalyzer:
        def analyze_chart(self, file_bytes):
            return {'success': False, 'error': 'SMCChartAnalyzer not available.'}

# cv2 - optional
try:
    import cv2
except ImportError:
    cv2 = None

try:
    from authlib.integrations.flask_client import OAuth as AuthlibOAuth
except ImportError:
    AuthlibOAuth = None  # type: ignore[misc, assignment]

# --- UTILITY IMPORTS ---
from utils.data_fetcher import DataFetcher
from utils.ai_predictor import AIPredictor
from utils.trading_api import TradingAPI
from utils.pattern_recognition import PatternRecognizer
# --- END UTILITY IMPORTS ---

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'your_secret_key_here_market_minds_2024'
app.config['SESSION_TYPE'] = 'filesystem'


@app.route("/health")
def healthcheck():
    """Must stay lightweight — Railway probes this before routing traffic."""
    return jsonify({"ok": True}), 200


def _feature_readiness_snapshot() -> dict:
    root = Path(__file__).resolve().parent
    checks: dict[str, dict] = {}

    # Database readiness: verify the configured DB is reachable.
    db_ok = False
    db_error = None
    try:
        with db.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        db_ok = True
    except Exception as e:
        db_error = str(e)
    checks["database"] = {
        "ok": db_ok,
        "uri": app.config.get("SQLALCHEMY_DATABASE_URI"),
        "error": db_error,
    }

    google_cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    google_csec = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    google_redirect = (os.environ.get("GOOGLE_REDIRECT_URI") or "").strip()
    checks["google_oauth"] = {
        "ok": bool(google_cid and google_csec),
        "redirect_uri_set": bool(google_redirect),
        "enabled_in_app": bool(app.config.get("GOOGLE_OAUTH_ENABLED")),
    }

    fp_ok = True
    fp_error = None
    try:
        importlib.import_module("financial_sentiment_v8.financial_sentiment_v8.fyp_enhanced.app")
    except Exception as e:
        fp_ok = False
        fp_error = str(e)
    checks["financialpulse"] = {"ok": fp_ok, "error": fp_error}

    missing_rl_inputs = _rl_missing_training_inputs(root)
    checks["rl_training_data"] = {
        "ok": not missing_rl_inputs,
        "missing_files": missing_rl_inputs,
    }

    return {
        "ok": all(c.get("ok", False) for c in checks.values()),
        "checks": checks,
    }


@app.route("/health/ready")
def health_ready():
    """
    Readiness endpoint for production debugging.
    Returns 200 only when core features are configured and reachable.
    """
    snap = _feature_readiness_snapshot()
    return jsonify(snap), (200 if snap["ok"] else 503)

# Pick up template edits without restarting (dev-friendly; disable in strict production if needed)
app.config['TEMPLATES_AUTO_RELOAD'] = os.environ.get('MARKETMINDS_DISABLE_TEMPLATE_RELOAD', '').lower() not in (
    '1',
    'true',
    'yes',
)


@app.after_request
def _rl_trading_no_cache(response):
    """Avoid stale HTML for /rl_trading (browser or CDN caching old template)."""
    if getattr(request, "endpoint", None) == "rl_trading":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _log_rl_template_marker():
    """At startup: prove which folder Flask loads templates from (common OneDrive duplicate-folder issue)."""
    base = Path(__file__).resolve().parent / "templates"
    p = base / "rl_trading.html"
    stray = base / "rl_agent_dashboard.html"
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
        ok = (
            "trading-v3" in txt
            and "data-mm-rl-ui" in txt
            and "rl_trading.js" in txt
            and "Production safeguards" not in txt
        )
        print(f"[RL] templates dir: {base}")
        print(f"[RL] {p.name} | modern copy={ok}")
        if stray.exists():
            print(f"[RL] NOTE: remove stray {stray.name} to avoid editing the wrong file.")
        if not ok:
            print("[RL] WARNING: rl_trading.html does not match the expected UI — wrong project folder or file damaged.")
    except OSError as e:
        print(f"[RL] WARNING: could not read {p}: {e}")


_log_rl_template_marker()


def _socketio_async_mode() -> str:
    explicit = (os.environ.get("SOCKETIO_ASYNC_MODE") or "").strip().lower()
    if explicit in ("threading", "eventlet", "gevent"):
        return explicit
    # Match Procfile / production: gunicorn gevent websocket worker.
    if os.environ.get("RENDER"):
        return "gevent"
    return "threading"


# ── Socket.IO (required for FinancialPulse real-time features) ──────────────
# Must be created on the MarketMinds app BEFORE integrating FinancialPulse
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=_socketio_async_mode(),
    logger=False,
    engineio_logger=False,
)

# Render: defer FP wiring so $PORT binds first. Event becomes set once FP HTTP/UI + /api/* clone exist — before Socket.IO
# handlers finish (those can stall under eventlet; fp-loading polls this flag).
_FP_INTEGRATION_READY = threading.Event()
_FP_INTEGRATION_STARTED = False
_FP_INTEGRATION_LOCK = threading.Lock()


@app.before_request
def _wait_financialpulse_on_render():
    if not os.environ.get("RENDER"):
        return None
    path = request.path or ""
    if (
        path == "/"
        or path == "/api/auth_status"
        or path == "/fp-loading"
        or path == "/api/fp-ready"
        or path == "/dashboard"
        or path.startswith("/static")
        or path.startswith("/favicon")
        or path.startswith("/socket.io")
        or path in ("/health", "/health/ready")
        or path.startswith("/login")
    ):
        return None
    if _FP_INTEGRATION_READY.is_set():
        return None
    # FP static is requested as parallel loads; do not 302 HTML into CSS/JS requests — fall through to quick 404 until mounted.
    if path.startswith("/financialpulse/static"):
        return None
    # Do not hold the TCP connection silent for minutes (browser shows endless "Loading…").
    if path == "/financialpulse" or path.startswith("/financialpulse/"):
        _ensure_fp_integration_started()
        return redirect(url_for("fp_loading_gate"))
    if not _FP_INTEGRATION_READY.wait(timeout=180):
        abort(503)
    return None


# Database Configuration (SQLite)
DatabaseConfig.init_app(app)

oauth = None


def _init_google_oauth() -> None:
    """Register Google OAuth when GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set."""
    global oauth
    oauth = None
    app.config["GOOGLE_OAUTH_ENABLED"] = False
    if AuthlibOAuth is None:
        print("[Auth] authlib not installed — Google sign-in disabled. pip install authlib")
        return
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    csec = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not cid or not csec:
        print("[Auth] Google OAuth disabled — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
        return
    oauth = AuthlibOAuth(app)
    oauth.register(
        name="google",
        client_id=cid,
        client_secret=csec,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    app.config["GOOGLE_OAUTH_ENABLED"] = True
    print("[Auth] Google sign-in enabled.")
    print(
        "[Auth] In Google Cloud, add BOTH redirect URIs unless GOOGLE_REDIRECT_URI is set:\n"
        "      http://127.0.0.1:5000/login/google/callback\n"
        "      http://localhost:5000/login/google/callback"
    )


_init_google_oauth()

# Flask-Login Configuration
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return UserService.get_user_by_id(user_id)


_FP_LOADING_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>MarketMinds — starting…</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8eaef;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.box{text-align:center;max-width:26rem;padding:1.75rem;}h1{font-size:1.15rem;font-weight:600;}p{opacity:.85;font-size:.95rem;line-height:1.45;}
</style></head><body><div class="box"><h1>Starting FinancialPulse…</h1>
<p>Models and routes load in the background after deploy or cold start. This page refreshes automatically.</p>
<p id="s">Checking readiness…</p></div>
<script>
async function tick(){try{const r=await fetch('/api/fp-ready',{cache:'no-store'});const j=await r.json();
if(j.ok){location.replace('/financialpulse');return;}
document.getElementById('s').textContent='Still loading…';}catch(e){document.getElementById('s').textContent='Network error — retrying…';}
setTimeout(tick,1100);}tick();
</script></body></html>"""


@app.route("/fp-loading")
@login_required
def fp_loading_gate():
    _ensure_fp_integration_started()
    if _FP_INTEGRATION_READY.is_set():
        return redirect("/financialpulse")
    return render_template_string(_FP_LOADING_HTML)


@app.route("/api/fp-ready")
def fp_ready_json():
    resp = jsonify(ok=_FP_INTEGRATION_READY.is_set())
    resp.headers["Cache-Control"] = "no-store"
    return resp


# Chart AI Configuration
app.config['UPLOAD_FOLDER'] = 'static/uploads/charts'
app.config['PROFILE_UPLOAD_FOLDER'] = 'static/uploads/profiles'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- INITIALIZATION ---
data_fetcher = DataFetcher()
trading_api = TradingAPI(data_fetcher)
pattern_recognizer = None
_pattern_recognizer_lock = threading.Lock()
_ai_predictor_lock = threading.Lock()


class _LazyAIPredictor:
    """Delay heavy model loading until first use to keep Render startup fast."""

    def __init__(self, model_path: str):
        self._model_path = model_path
        self._instance = None

    def _get(self) -> AIPredictor:
        if self._instance is None:
            with _ai_predictor_lock:
                if self._instance is None:
                    self._instance = AIPredictor(model_path=self._model_path)
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get(), name)


ai_predictor = _LazyAIPredictor(model_path='models_unified/')


def get_pattern_recognizer():
    """Lazy-load pattern model so web server can bind port quickly on Render."""
    global pattern_recognizer
    if pattern_recognizer is not None:
        return pattern_recognizer
    with _pattern_recognizer_lock:
        if pattern_recognizer is None:
            pattern_recognizer = PatternRecognizer(model_path='best.pt')
    return pattern_recognizer

LIVE_ECONOMIC_EVENT = {
    'event_type': 'FOMC',
    'event_actual': 3.65,
    'event_expected': 3.75,
    'event_previous': 3.875,
    'raw_surprise': -0.05,
    'CPI_SURPRISE': -0.032,
    'NFP_SURPRISE': 1.24,
    'PPI_SURPRISE': 0.0,
    'FOMC_SURPRISE': -0.05,
}

@app.context_processor
def inject_user():
    return dict(current_user=current_user if current_user.is_authenticated else None, now=datetime.now())

# ====================================================================
# CORE ROUTES
# ====================================================================

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    # Keep post-login path fast; FinancialPulse can be opened on demand from dashboard/nav.
    return redirect(url_for("dashboard"))

@app.route('/api/auth_status')
def auth_status():
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'username': current_user.username,
            'profile_picture': current_user.profile_picture or 'default_avatar.png'
        })
    return jsonify({'authenticated': False})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember_me = request.form.get('remember_me') == 'on'

        if not username or not password:
            return render_template('login.html', error='Please fill in all fields')

        user, error = UserService.authenticate_user(username, password)

        if error:
            return render_template('login.html', error=error)

        login_user(user, remember=remember_me)
        flash(f'Welcome back, {user.username}!', 'success')

        next_page = request.args.get('next')
        return redirect(next_page) if next_page else redirect(url_for('dashboard'))

    return render_template('login.html')


@app.route("/login/google")
def login_google():
    if not app.config.get("GOOGLE_OAUTH_ENABLED") or oauth is None:
        flash("Google sign-in is not configured on this server.", "error")
        return redirect(url_for("login"))
    # Must match Google Cloud "Authorized redirect URIs" exactly (host + path).
    env_uri = (os.environ.get("GOOGLE_REDIRECT_URI") or "").strip()
    if env_uri:
        redirect_uri = env_uri
    else:
        # Use same host the user opened (localhost vs 127.0.0.1) — register BOTH in Google Cloud.
        redirect_uri = request.url_root.rstrip("/") + url_for(
            "login_google_callback", _external=False
        )
    print(f"[Auth] Google OAuth redirect_uri (must match Google Cloud → Authorized redirect URIs): {redirect_uri}")
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def login_google_callback():
    if not app.config.get("GOOGLE_OAUTH_ENABLED") or oauth is None:
        return redirect(url_for("login"))
    try:
        token = oauth.google.authorize_access_token()
        resp = oauth.google.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            token=token,
        )
        profile = resp.json()
    except Exception as e:
        print(f"[Auth] Google callback error: {e}")
        flash("Google sign-in was cancelled or failed.", "error")
        return redirect(url_for("login"))

    user, err = UserService.get_or_create_from_google(profile)
    if err or not user:
        flash(err or "Could not complete Google sign-in.", "error")
        return redirect(url_for("login"))
    if not user.is_active:
        flash("This account is deactivated.", "error")
        return redirect(url_for("login"))

    login_user(user, remember=True)
    flash(f"Welcome, {user.username}!", "success")
    next_page = request.args.get("next")
    return redirect(next_page) if next_page else redirect(url_for("dashboard"))


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()

        errors = []
        if password != confirm_password:
            errors.append('Passwords do not match')

        if errors:
            return render_template('signup.html', errors=errors)

        user, error = UserService.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name if first_name else None,
            last_name=last_name if last_name else None
        )

        if error:
            return render_template('signup.html', errors=[error])

        flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        crypto_data = data_fetcher.get_crypto_data()
        stocks_data = data_fetcher.get_stocks_data()
        forex_data = data_fetcher.get_forex_data()
    except Exception as e:
        print(f"Error fetching market data for dashboard: {e}")
        crypto_data = []
        stocks_data = []
        forex_data = []

    def _to_float(value, default=0.0):
        try:
            if isinstance(value, str):
                v = value.replace('%', '').replace(',', '').strip()
                return float(v) if v else default
            return float(value)
        except Exception:
            return default

    market_items = (crypto_data or []) + (stocks_data or [])
    if market_items:
        portfolio_value = sum(_to_float(item.get('price', 0)) for item in market_items[:6])
        avg_change = sum(_to_float(item.get('change_percent', 0)) for item in market_items[:6]) / max(len(market_items[:6]), 1)
        todays_gain = (portfolio_value * avg_change) / 100.0
        active_positions = len(market_items[:6])
        best_performer = max(market_items, key=lambda x: _to_float(x.get('change_percent', 0)))
        best_symbol = best_performer.get('symbol', '--')
        best_change = _to_float(best_performer.get('change_percent', 0))
    else:
        portfolio_value = 0.0
        avg_change = 0.0
        todays_gain = 0.0
        active_positions = 0
        best_symbol = '--'
        best_change = 0.0

    return render_template('dashboard.html',
                         crypto_data=crypto_data,
                         stocks_data=stocks_data,
                         forex_data=forex_data,
                         username=current_user.username,
                         portfolio_value=portfolio_value,
                         avg_change=avg_change,
                         todays_gain=todays_gain,
                         active_positions=active_positions,
                         best_symbol=best_symbol,
                         best_change=best_change)

@app.route('/news_ai')
@login_required
def news_ai():
    return render_template('news_ai.html', username=current_user.username)

# ====================================================================
# USER PROFILE ROUTES
# ====================================================================

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip().lower()

        success, error = UserService.update_profile(
            current_user,
            first_name=first_name if first_name else None,
            last_name=last_name if last_name else None,
            email=email if email else None
        )

        if error:
            flash(error, 'error')
        else:
            flash('Profile updated successfully!', 'success')

        return redirect(url_for('profile'))

    return render_template('edit_profile.html', user=current_user)

@app.route('/profile/change-username', methods=['POST'])
@login_required
def change_username():
    new_username = request.form.get('new_username', '').strip()

    if not new_username:
        flash('Username cannot be empty', 'error')
        return redirect(url_for('edit_profile'))

    success, error = UserService.update_username(current_user, new_username)

    if error:
        flash(error, 'error')
    else:
        flash('Username changed successfully!', 'success')

    return redirect(url_for('edit_profile'))

@app.route('/profile/upload-picture', methods=['POST'])
@login_required
def upload_profile_picture():
    if 'profile_picture' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('edit_profile'))

    file = request.files['profile_picture']
    filename, error = UserService.upload_profile_picture(current_user, file)

    if error:
        flash(error, 'error')
    else:
        flash('Profile picture uploaded successfully!', 'success')

    return redirect(url_for('edit_profile'))

@app.route('/profile/change-password', methods=['POST'])
@login_required
def change_password():
    old_password = request.form.get('old_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if new_password != confirm_password:
        flash('New passwords do not match', 'error')
        return redirect(url_for('edit_profile'))

    success, error = UserService.change_password(current_user, old_password, new_password)

    if error:
        flash(error, 'error')
    else:
        flash('Password changed successfully!', 'success')

    return redirect(url_for('edit_profile'))

@app.route('/static/uploads/profiles/<filename>')
def serve_profile_picture(filename):
    from flask import send_from_directory, abort
    upload_folder = os.path.join('static', 'uploads', 'profiles')
    if os.path.exists(os.path.join(upload_folder, filename)):
        return send_from_directory(upload_folder, filename)
    else:
        abort(404)

@app.route('/chart_ai', methods=['GET'])
@login_required
def chart_ai():
    return render_template('chart_ai.html', username=current_user.username)

# ====================================================================
# PATTERN RECOGNITION ROUTES
# ====================================================================

@app.route('/pattern_recognition', methods=['GET'])
@login_required
def pattern_recognition():
    return render_template('pattern_recognition.html', username=current_user.username)

@app.route('/api/recognize_patterns', methods=['POST'])
def recognize_patterns():
    try:
        if 'chart_image' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        file = request.files['chart_image']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        pr = get_pattern_recognizer()
        if not pr.allowed_file(file.filename):
            return jsonify({
                'success': False,
                'error': 'Invalid file type. Allowed types are png, jpg, jpeg, gif.'
            }), 400

        file_bytes = file.read()
        result = pr.predict(file_bytes, file.filename)
        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'An internal server error occurred: {str(e)}'
        }), 500

# ====================================================================
# AI PREDICTION ROUTES
# ====================================================================

@app.route('/ai_picks')
@login_required
def ai_picks():
    if not ai_predictor.is_ready:
        return render_template('ai_picks_error.html',
                               error_message="Model Not Loaded. Please check that 'models_unified' folder and its PKL/TXT files are correctly placed.",
                               username=current_user.username)

    full_symbols = ai_predictor.full_symbol_list

    try:
        stocks_data = data_fetcher.get_stocks_data(full_symbols['Stocks'])
        crypto_data = data_fetcher.get_crypto_data(full_symbols['Crypto'])
        forex_data = data_fetcher.get_forex_data(full_symbols['Forex'])
        commodity_data = data_fetcher.get_commodity_data(full_symbols['Commodities'])
    except Exception as e:
        print(f"Error fetching real-time data for AI Picks: {e}")
        stocks_data = []
        crypto_data = []
        forex_data = []
        commodity_data = []

    all_data = []
    for d in stocks_data + crypto_data + forex_data + commodity_data:
        if d.get('price') is not None and d.get('price') != 0 and d.get('symbol') and d.get('category'):
            all_data.append(d)

    signals = ai_predictor.predict_signals(all_data, LIVE_ECONOMIC_EVENT)

    strong_picks = [s for s in signals if s['Hybrid_Score'] is not None and abs(s['Hybrid_Score']) >= 0.0005]
    strong_symbols = {s['symbol'] for s in strong_picks}

    hold_signals = []
    for item in all_data:
        if item['symbol'] not in strong_symbols:
            h = item.copy()
            h['Signal'] = 'HOLD'
            h['Hybrid_Score'] = 0.0000
            h['confidence'] = 55.0
            if 'name' not in h: h['name'] = h['symbol']
            if 'price' not in h: h['price'] = 0.0
            hold_signals.append(h)

    return render_template('ai_picks.html',
                           strong_signals=strong_picks,
                           hold_signals=hold_signals,
                           username=current_user.username,
                           last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/api/ai_picks_ticker')
def ai_picks_ticker():
    if not ai_predictor.is_ready:
        return jsonify([])

    try:
        stocks_symbols = data_fetcher._extract_symbols(ai_predictor.full_symbol_list['Stocks'])[:3]
        crypto_symbols = data_fetcher._extract_symbols(ai_predictor.full_symbol_list['Crypto'])[:3]

        stocks_data = data_fetcher.get_stocks_data(stocks_symbols)
        crypto_data = data_fetcher.get_crypto_data(crypto_symbols)

        all_data = [d for d in stocks_data + crypto_data
                    if d.get('price') is not None and d.get('price') != 0 and d.get('symbol') and d.get('category')]

        if not all_data:
            return jsonify([])

        signals = ai_predictor.predict_signals(all_data, LIVE_ECONOMIC_EVENT)

        buy_signals = sorted([s for s in signals if s['Signal'] == 'BUY' and s['Hybrid_Score'] is not None],
                             key=lambda x: x['Hybrid_Score'], reverse=True)

        ticker_data = []
        for s in buy_signals[:5]:
            price = s.get('price', 0.0)
            if s['category'] == 'Forex':
                price_str = f"{price:.4f}"
            elif s['category'] == 'Crypto' and price > 100:
                price_str = f"${price:.0f}"
            else:
                price_str = f"${price:.2f}"

            ticker_data.append({
                'symbol': s['symbol'],
                'price': price_str,
                'signal': s['Signal'],
                'confidence': f"{s.get('confidence', 55.0):.0f}%",
                'score': f"{s.get('Hybrid_Score', 0.0):+.2f}"
            })

        return jsonify(ticker_data)
    except Exception as e:
        print(f"FATAL ERROR processing AI Picks for ticker: {e}")
        return jsonify([])

@app.route('/chart/<symbol_type>/<symbol>')
@login_required
def chart_view(symbol_type, symbol):
    return render_template('chart_view.html',
                         symbol_type=symbol_type,
                         symbol=symbol,
                         symbol_data={'symbol': symbol, 'name': symbol, 'price': 0, 'change': 0, 'change_percent': 0})

# ====================================================================
# API ENDPOINTS
# ====================================================================

@app.route('/api/analyze_chart', methods=['POST'])
def analyze_chart():
    try:
        if 'chart_image' not in request.files:
            return jsonify({'success': False, 'error': 'No file part in the request.'}), 400

        file = request.files['chart_image']

        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected for uploading.'}), 400

        if file and allowed_file(file.filename):
            file_bytes = file.read()
            analyzer = SMCChartAnalyzer()
            result = analyzer.analyze_chart(file_bytes)
            return jsonify(result)
        else:
            return jsonify({
                'success': False,
                'error': 'Invalid file type. Allowed types are png, jpg, jpeg, gif.'
            }), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'An internal server error occurred: {str(e)}'
        }), 500

@app.route('/api/smc_info')
def smc_info():
    smc_concepts = {
        'order_blocks': {
            'name': 'Order Blocks (OB)',
            'description': 'Areas where institutional orders are placed. These zones often act as strong support or resistance.',
            'bullish': 'Forms after a strong bullish impulse move, expect price to bounce from this zone.',
            'bearish': 'Forms after a strong bearish impulse move, expect price to reject from this zone.'
        },
        'fair_value_gaps': {
            'name': 'Fair Value Gaps (FVG)',
            'description': 'Price imbalances that occur during strong moves. Price often returns to fill these gaps.',
            'bullish': 'Gap created during bullish move, price may return to fill it before continuing up.',
            'bearish': 'Gap created during bearish move, price may return to fill it before continuing down.'
        },
        'break_of_structure': {
            'name': 'Break of Structure (BOS)',
            'description': 'When price breaks a significant high or low, indicating trend continuation.',
            'bullish': 'Price breaks above previous high - bullish trend continues.',
            'bearish': 'Price breaks below previous low - bearish trend continues.'
        },
        'change_of_character': {
            'name': 'Change of Character (CHoCH)',
            'description': 'First sign of potential trend reversal when price fails to make a higher high or lower low.',
            'signal': 'Watch for confirmation with order blocks or FVG.'
        },
        'support_resistance': {
            'name': 'Support & Resistance',
            'description': 'Key price levels where price has historically reacted.',
            'support': 'Level where price tends to bounce up (buyers step in).',
            'resistance': 'Level where price tends to reverse down (sellers step in).'
        }
    }
    return jsonify(smc_concepts)

@app.route('/api/market_data/<symbol_type>/<symbol>')
def get_market_data(symbol_type, symbol):
    return jsonify({'error': 'Endpoint not implemented'})

@app.route('/api/search')
def search_symbols_api():
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])
    search_results = data_fetcher.search_symbols(query)
    return jsonify(search_results)

@app.route('/api/technical_analysis', methods=['POST'])
def technical_analysis():
    return jsonify({'error': 'Endpoint not implemented'})

@app.route('/api/analyze_news_sentiment', methods=['POST'])
def analyze_news_sentiment():
    return jsonify({'error': 'Endpoint not implemented'})

@app.route('/api/search_symbols')
def search_symbols():
    query = request.args.get('q', '').upper()

    popular_symbols = {
        'STOCKS': ['AAPL', 'TSLA', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'AMD', 'NFLX', 'SPY', 'QQQ'],
        'CRYPTO': ['BTC-USD', 'ETH-USD', 'ADA-USD', 'DOT-USD', 'LINK-USD', 'BNB-USD'],
        'FOREX': ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X', 'AUDUSD=X'],
        'INDICES': ['^GSPC', '^DJI', '^IXIC', '^RUT']
    }

    results = []
    for category, symbols in popular_symbols.items():
        for symbol in symbols:
            if query in symbol:
                results.append({
                    'symbol': symbol,
                    'category': category,
                    'name': symbol
                })

    return jsonify(results[:10])

@app.route('/api/market_overview')
def market_overview():
    try:
        indices = {
            'SPY': 'S&P 500',
            'QQQ': 'NASDAQ 100',
            'DIA': 'Dow Jones',
            'IWM': 'Russell 2000'
        }

        overview = []
        for symbol, name in indices.items():
            try:
                stock = yf.Ticker(symbol)
                hist = stock.history(period='2d')

                if len(hist) >= 2:
                    current = hist['Close'].iloc[-1]
                    previous = hist['Close'].iloc[-2]
                    change = current - previous
                    change_pct = (change / previous) * 100

                    overview.append({
                        'symbol': symbol,
                        'name': name,
                        'price': round(current, 2),
                        'change': round(change, 2),
                        'change_percent': round(change_pct, 2),
                        'trend': 'up' if change > 0 else 'down'
                    })
            except:
                continue

        return jsonify(overview)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ====================================================================
# REINFORCEMENT LEARNING (Double DQN) — see rl/ package
# ====================================================================
_rl_training_active = False
_rl_training_thread: threading.Thread | None = None
_RL_TRAIN_LOCK = threading.Lock()
_RL_AUTO_THREAD_STARTED = False


def _rl_auto_train_state_path(models_dir: Path) -> Path:
    return models_dir / "rl_auto_train_state.json"


def _rl_read_auto_train_state(models_dir: Path) -> dict:
    p = _rl_auto_train_state_path(models_dir)
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def _rl_write_auto_train_state(models_dir: Path, payload: dict) -> None:
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
        p = _rl_auto_train_state_path(models_dir)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except OSError:
        pass


def _rl_auto_train_interval_timedelta() -> timedelta:
    """
    How long to wait after a successful auto-train before the next one.
    Precedence: RL_AUTO_TRAIN_INTERVAL_MINUTES, then RL_AUTO_TRAIN_INTERVAL_HOURS,
    else default once per day (24h). Use RL_AUTO_TRAIN_INTERVAL_MINUTES for shorter dev cycles.
    """
    if "RL_AUTO_TRAIN_INTERVAL_MINUTES" in os.environ:
        try:
            m = int(os.environ.get("RL_AUTO_TRAIN_INTERVAL_MINUTES", "1"))
        except ValueError:
            m = 1
        m = max(1, min(m, 60 * 24 * 14))
        return timedelta(minutes=m)
    if "RL_AUTO_TRAIN_INTERVAL_HOURS" in os.environ:
        try:
            h = int(os.environ.get("RL_AUTO_TRAIN_INTERVAL_HOURS", "24"))
        except ValueError:
            h = 24
        h = max(1, min(h, 24 * 14))
        return timedelta(hours=h)
    return timedelta(hours=24)


def _rl_format_auto_train_interval(td: timedelta) -> str:
    sec = int(td.total_seconds())
    if sec <= 0:
        return "1 minute"
    if sec < 3600 and sec % 60 == 0:
        m = sec // 60
        return "1 minute" if m == 1 else f"{m} minutes"
    if sec % 3600 == 0:
        h = sec // 3600
        return "1 hour" if h == 1 else f"{h} hours"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec / 3600:.1f} hours"


def _rl_auto_train_initial_delay_sec() -> float:
    raw = os.environ.get("RL_AUTO_TRAIN_INITIAL_DELAY_SEC", "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 86_400.0))
        except ValueError:
            pass
    sec = _rl_auto_train_interval_timedelta().total_seconds()
    if sec <= 120.0:
        return max(5.0, min(30.0, sec * 0.5))
    return max(30.0, min(120.0, sec / 10.0))


def _rl_auto_train_poll_sec() -> float:
    raw = os.environ.get("RL_AUTO_TRAIN_POLL_SEC", "").strip()
    if raw:
        try:
            return max(10.0, min(float(raw), 86_400.0))
        except ValueError:
            pass
    sec = _rl_auto_train_interval_timedelta().total_seconds()
    return max(10.0, min(3600.0, max(15.0, sec / 4.0)))


def _rl_auto_train_disabled() -> bool:
    return os.environ.get("RL_AUTO_TRAIN_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _rl_iso_utc_short_display(iso: str | None) -> str | None:
    """User-facing UTC timestamp without microseconds noise."""
    if not iso or not isinstance(iso, str) or not iso.strip():
        return None
    try:
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError, OSError):
        return None


def _rl_auto_train_status(models_dir: Path) -> dict:
    td = _rl_auto_train_interval_timedelta()
    interval_label = _rl_format_auto_train_interval(td)
    interval_minutes = max(1, int(round(td.total_seconds() / 60.0)))
    st = _rl_read_auto_train_state(models_dir)
    last = st.get("last_auto_train_utc")
    next_due: str | None = None
    if isinstance(last, str) and last:
        try:
            raw = last[:-1] + "+00:00" if last.endswith("Z") else last
            lu = datetime.fromisoformat(raw)
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            next_due = (lu + td).isoformat()
        except (ValueError, TypeError):
            next_due = None
    last_iso = last if isinstance(last, str) else None
    missing_inputs = _rl_missing_training_inputs(Path(__file__).resolve().parent)
    return {
        "enabled": (not _rl_auto_train_disabled()) and (not missing_inputs),
        "interval_label": interval_label,
        "interval_minutes": interval_minutes,
        "interval_hours": td.total_seconds() / 3600.0,
        "last_auto_train_utc": last_iso,
        "last_run_display": _rl_iso_utc_short_display(last_iso),
        "next_due_utc": next_due,
        "next_due_display": _rl_iso_utc_short_display(next_due),
        "episodes": int(os.environ.get("RL_AUTO_TRAIN_EPISODES", "4") or 4),
        "max_rows_default": int(os.environ.get("RL_AUTO_TRAIN_MAX_ROWS", "25000") or 25000),
        "missing_training_inputs": missing_inputs,
    }


def _rl_training_input_paths(root: Path) -> tuple[Path, Path]:
    return (root / "X_features_unified.csv", root / "unified_training_data.csv")


def _rl_configured_dataset_url(*env_keys: str) -> str:
    for key in env_keys:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return ""


def _rl_download_if_missing(target_path: Path, *env_keys: str) -> bool:
    """
    Try to hydrate a missing RL dataset from a configured URL.
    Returns True only when target_path exists after this call.
    """
    if target_path.exists():
        return True
    url = _rl_configured_dataset_url(*env_keys)
    if not url:
        return False
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as resp:  # nosec B310 - URL is admin-configured env
            code = getattr(resp, "status", 200) or 200
            if code >= 400:
                raise OSError(f"HTTP {code}")
            with open(target_path, "wb") as out:
                shutil.copyfileobj(resp, out)
        if not target_path.exists() or target_path.stat().st_size == 0:
            raise OSError("Downloaded file is empty")
        print(f"[RL data] Downloaded {target_path.name} from configured URL.")
        return True
    except Exception as e:
        print(f"[RL data] Failed to download {target_path.name}: {e}")
        return target_path.exists()


def _rl_missing_training_inputs(root: Path) -> list[str]:
    x_path, p_path = _rl_training_input_paths(root)
    _rl_download_if_missing(
        x_path,
        "RL_X_FEATURES_URL",
        "X_FEATURES_UNIFIED_URL",
    )
    _rl_download_if_missing(
        p_path,
        "RL_UNIFIED_TRAINING_URL",
        "UNIFIED_TRAINING_DATA_URL",
    )
    missing: list[str] = []
    if not x_path.exists():
        missing.append(x_path.name)
    if not p_path.exists():
        missing.append(p_path.name)
    return missing


def _log_rl_dataset_source_status() -> None:
    root = Path(__file__).resolve().parent
    x_path, p_path = _rl_training_input_paths(root)
    x_url = _rl_configured_dataset_url("RL_X_FEATURES_URL", "X_FEATURES_UNIFIED_URL")
    p_url = _rl_configured_dataset_url("RL_UNIFIED_TRAINING_URL", "UNIFIED_TRAINING_DATA_URL")
    _rl_missing_training_inputs(root)
    print(
        "[RL data] startup | "
        f"{x_path.name}: {'present' if x_path.exists() else 'missing'} "
        f"(url={'set' if x_url else 'unset'}) | "
        f"{p_path.name}: {'present' if p_path.exists() else 'missing'} "
        f"(url={'set' if p_url else 'unset'})"
    )


def _rl_maybe_run_scheduled_training() -> None:
    """Background scheduler: start a training job when the interval has elapsed."""
    if _rl_auto_train_disabled():
        return
    root = Path(__file__).resolve().parent
    missing_inputs = _rl_missing_training_inputs(root)
    if missing_inputs:
        print(
            "[RL auto-train] skipped: missing input file(s): "
            + ", ".join(missing_inputs)
        )
        return
    models_dir = root / "rl" / "models"
    interval_td = _rl_auto_train_interval_timedelta()
    st = _rl_read_auto_train_state(models_dir)
    last = st.get("last_auto_train_utc")
    now = datetime.now(timezone.utc)
    due = False
    if not isinstance(last, str) or not last.strip():
        due = True
    else:
        try:
            s = last[:-1] + "+00:00" if last.endswith("Z") else last
            lu = datetime.fromisoformat(s)
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            due = (now - lu) >= interval_td
        except (ValueError, TypeError):
            due = True
    if not due:
        return
    try:
        ep = int(os.environ.get("RL_AUTO_TRAIN_EPISODES", "4"))
    except ValueError:
        ep = 4
    ep = max(1, min(ep, 100))
    try:
        mr = int(os.environ.get("RL_AUTO_TRAIN_MAX_ROWS", "25000"))
    except ValueError:
        mr = 25000
    mr = max(1000, min(mr, 49_999))
    use_existing = os.environ.get("RL_AUTO_TRAIN_USE_EXISTING", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    dueling = os.environ.get("RL_AUTO_TRAIN_DUELING", "").strip().lower() in ("1", "true", "yes")
    prioritized = os.environ.get("RL_AUTO_TRAIN_PRIORITIZED", "").strip().lower() in ("1", "true", "yes")
    ack = os.environ.get("RL_AUTO_TRAIN_ACK_FULL", "").strip().lower() in ("1", "true", "yes")
    _rl_start_training_worker(
        episodes=ep,
        use_existing=use_existing,
        max_rows=mr,
        training_acknowledged=ack,
        dueling=dueling,
        prioritized=prioritized,
        trigger="auto",
    )


def _rl_start_training_worker(
    *,
    episodes: int,
    use_existing: bool,
    max_rows: int | None,
    training_acknowledged: bool,
    dueling: bool,
    prioritized: bool,
    trigger: str,
) -> bool:
    """Return True if a worker was started."""
    global _rl_training_active, _rl_training_thread
    with _RL_TRAIN_LOCK:
        _rl_sync_training_flag()
        if _rl_training_active:
            return False
        worker = threading.Thread(
            target=_rl_training_job,
            args=(
                episodes,
                use_existing,
                max_rows,
                training_acknowledged,
                dueling,
                prioritized,
                trigger,
            ),
            daemon=True,
        )
        _rl_training_thread = worker
        _rl_training_active = True
        worker.start()
    return True


def _rl_auto_train_loop() -> None:
    """Wake periodically and enqueue scheduled RL training when due."""
    delay = _rl_auto_train_initial_delay_sec()
    time.sleep(delay)
    poll = _rl_auto_train_poll_sec()
    while True:
        try:
            _rl_maybe_run_scheduled_training()
        except Exception as e:
            print(f"[RL auto-train] loop error: {e}")
        time.sleep(poll)


def _ensure_rl_auto_train_thread() -> None:
    global _RL_AUTO_THREAD_STARTED
    if _RL_AUTO_THREAD_STARTED:
        return
    if _rl_auto_train_disabled():
        print("[RL auto-train] disabled (RL_AUTO_TRAIN_DISABLED).")
        return
    _RL_AUTO_THREAD_STARTED = True
    t = threading.Thread(target=_rl_auto_train_loop, daemon=True, name="rl-auto-train")
    t.start()
    _td = _rl_auto_train_interval_timedelta()
    print(
        f"[RL auto-train] scheduler on — every {_rl_format_auto_train_interval(_td)} "
        f"(poll {_rl_auto_train_poll_sec():.0f}s; set RL_AUTO_TRAIN_INTERVAL_MINUTES, "
        f"RL_AUTO_TRAIN_INTERVAL_HOURS, or RL_AUTO_TRAIN_DISABLED to adjust)."
    )


def _rl_sync_training_flag() -> None:
    """Clear busy flag if the worker is gone (finished thread, or wedged active with no thread ref)."""
    global _rl_training_active, _rl_training_thread
    if not _rl_training_active:
        return
    if _rl_training_thread is None:
        _rl_training_active = False
        return
    if not _rl_training_thread.is_alive():
        _rl_training_active = False
        _rl_training_thread = None


@app.route("/rl_trading")
@app.route("/rl_agent")
@login_required
def rl_trading():
    with _RL_TRAIN_LOCK:
        _rl_sync_training_flag()
    from rl.inference import (
        apply_circuit_buy_halt,
        compute_rl_signals,
        load_agent_and_scaler,
        read_current_agent_meta,
        read_metrics_json,
        rl_paths,
    )
    from rl.production import circuit_halt_buy, load_circuit_state, refresh_circuit_from_paper_trades, resolve_checkpoint_paths
    from rl.reinforcement_digest import read_reinforcement_digest, refresh_reinforcement_digest

    _ensure_rl_auto_train_thread()

    paths = rl_paths()
    root = paths["models_dir"]
    trained = resolve_checkpoint_paths(root)[0] is not None
    refresh_circuit_from_paper_trades(root)
    metrics_blob = read_metrics_json()
    val_metrics = (metrics_blob or {}).get("validation") if metrics_blob else None
    metrics_generated_at_utc = (metrics_blob or {}).get("metrics_generated_at_utc") if metrics_blob else None
    rl_metrics_n_val = (metrics_blob or {}).get("n_val_rows") if metrics_blob else None
    rl_last_run = _rl_read_last_run_status(root)
    epsilon_meta = (metrics_blob or {}).get("epsilon") if metrics_blob else None
    checkpoint_meta = read_current_agent_meta(root)
    circuit_state = load_circuit_state(root) or {}

    rl_digest = read_reinforcement_digest(root)
    if rl_digest is None:
        rl_digest = refresh_reinforcement_digest(
            root,
            validation_metrics=val_metrics if isinstance(val_metrics, dict) else None,
        )
    rl_auto_train = _rl_auto_train_status(root)

    agent, scaler = (None, None)
    if trained:
        agent, scaler = load_agent_and_scaler()

    full_symbols = ai_predictor.full_symbol_list
    try:
        stocks_data = data_fetcher.get_stocks_data(full_symbols["Stocks"])
        crypto_data = data_fetcher.get_crypto_data(full_symbols["Crypto"])
        forex_data = data_fetcher.get_forex_data(full_symbols["Forex"])
        commodity_data = data_fetcher.get_commodity_data(full_symbols["Commodities"])
    except Exception as e:
        print(f"RL page: error fetching market data: {e}")
        stocks_data, crypto_data, forex_data, commodity_data = [], [], [], []

    all_data = []
    for d in stocks_data + crypto_data + forex_data + commodity_data:
        if d.get("price") is not None and d.get("price") != 0 and d.get("symbol") and d.get("category"):
            all_data.append(d)

    rl_signals = []
    if agent is not None and scaler is not None and all_data and ai_predictor.feature_cols:
        try:
            rl_signals = compute_rl_signals(ai_predictor, all_data, LIVE_ECONOMIC_EVENT, agent, scaler)
            rl_signals, _ = apply_circuit_buy_halt(rl_signals, circuit_halt_buy(root))
        except Exception as e:
            print(f"RL inference error: {e}")

    html = render_template(
        "rl_trading.html",
        username=current_user.username,
        trained=trained,
        val_metrics=val_metrics,
        epsilon_meta=epsilon_meta,
        rl_signals=rl_signals,
        checkpoint_meta=checkpoint_meta,
        circuit_state=circuit_state,
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        rl_last_run=rl_last_run,
        metrics_generated_at_utc=metrics_generated_at_utc,
        rl_metrics_n_val=rl_metrics_n_val,
        rl_digest=rl_digest,
        rl_auto_train=rl_auto_train,
    )
    out = make_response(html)
    out.headers["X-MM-RL-Build"] = "trading-v4"
    out.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    out.headers["Pragma"] = "no-cache"
    return out


def _rl_metrics_for_api(raw: dict | None) -> dict | None:
    """Expose both canonical keys (sharpe_ratio, …) and short aliases (sharpe, return_pct, …)."""
    if not raw or not isinstance(raw, dict):
        return None
    m = dict(raw)
    m.setdefault("sharpe", m.get("sharpe_ratio"))
    m.setdefault("return_pct", m.get("total_return_pct"))
    m.setdefault("max_drawdown", m.get("max_drawdown_pct"))
    return m


def _rl_write_last_run_status(
    models_dir: Path, error: str | None, *, trigger: str = "manual"
) -> None:
    """Persist outcome of the last background training job (for RL page + /api/rl/status)."""
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
        p = models_dir / "rl_last_run.json"
        payload = {
            "ok": error is None,
            "error": error,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger,
        }
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except OSError:
        pass


def _rl_read_last_run_status(models_dir: Path) -> dict | None:
    p = models_dir / "rl_last_run.json"
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, TypeError):
        return None


@app.route("/api/rl/status", methods=["GET"])
@login_required
def api_rl_status():
    from rl.inference import read_current_agent_meta, read_metrics_json, rl_paths
    from rl.production import circuit_halt_buy, load_circuit_state, resolve_checkpoint_paths

    paths = rl_paths()
    root = paths["models_dir"]
    trained = resolve_checkpoint_paths(root)[0] is not None
    blob = read_metrics_json()
    val_raw = (blob or {}).get("validation") if blob else None
    metrics = _rl_metrics_for_api(val_raw) if val_raw else None
    epsilon = (blob or {}).get("epsilon") if blob else None
    with _RL_TRAIN_LOCK:
        _rl_sync_training_flag()
        training_busy = _rl_training_active
    return jsonify(
        {
            "trained": trained,
            "metrics": metrics,
            "has_validation_metrics": bool(val_raw),
            "epsilon": epsilon,
            "training_active": training_busy,
            "checkpoint": read_current_agent_meta(root),
            "circuit": load_circuit_state(root),
            "circuit_halt_buy": circuit_halt_buy(root),
            "last_run": _rl_read_last_run_status(root),
            "auto_train": _rl_auto_train_status(root),
        }
    )


def _rl_training_job(
    episodes: int,
    use_existing: bool,
    max_rows: int | None,
    training_acknowledged: bool,
    dueling: bool,
    prioritized: bool,
    trigger: str = "manual",
) -> None:
    global _rl_training_active, _rl_training_thread
    root = Path(__file__).resolve().parent
    models_dir = root / "rl" / "models"
    log_path = models_dir / "training_log.txt"
    err_summary: str | None = None
    try:
        from rl.train import train_rl_agent
        x_path, price_path = _rl_training_input_paths(root)
        missing_inputs = _rl_missing_training_inputs(root)
        if missing_inputs:
            raise FileNotFoundError(
                "Missing RL input file(s): "
                + ", ".join(missing_inputs)
                + ". Upload/generate them in the app root before training."
            )

        train_rl_agent(
            x_path,
            price_path,
            output_dir=models_dir,
            n_episodes=episodes,
            log_file=log_path,
            use_existing=use_existing,
            max_rows=max_rows,
            full_training_acknowledged=training_acknowledged,
            dueling=dueling,
            prioritized=prioritized,
        )
    except Exception as e:
        err_summary = str(e)
        print(f"RL training error: {e}")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"ERROR: {e}\n")
        except OSError:
            pass
    finally:
        _rl_training_active = False
        _rl_training_thread = None
        _rl_write_last_run_status(models_dir, err_summary, trigger=trigger)
        if err_summary is None:
            try:
                from rl.inference import read_metrics_json
                from rl.reinforcement_digest import refresh_reinforcement_digest

                blob = read_metrics_json() or {}
                vm = blob.get("validation") if isinstance(blob, dict) else None
                refresh_reinforcement_digest(
                    models_dir,
                    validation_metrics=vm if isinstance(vm, dict) else None,
                )
            except Exception as dig_e:
                print(f"RL reinforcement digest warning: {dig_e}")
        if err_summary is None and trigger == "auto":
            prev = _rl_read_auto_train_state(models_dir)
            prev["last_auto_train_utc"] = datetime.now(timezone.utc).isoformat()
            prev["interval_seconds"] = int(_rl_auto_train_interval_timedelta().total_seconds())
            _rl_write_auto_train_state(models_dir, prev)
    if trigger == "auto" and err_summary:
        print(f"[RL auto-train] run failed, will retry on next poll: {err_summary}")


@app.route("/api/rl/train", methods=["POST"])
@login_required
def api_rl_train():
    data = request.get_json(silent=True) or {}
    n = int(data.get("episodes", 5))
    use_existing = bool(data.get("use_existing", False))
    n = max(1, min(n, 500))
    max_rows = data.get("max_rows")
    if max_rows is not None:
        max_rows = int(max_rows)
    training_acknowledged = bool(data.get("training_acknowledged", False))
    dueling = bool(data.get("dueling", False))
    prioritized = bool(data.get("prioritized", False))
    root = Path(__file__).resolve().parent
    missing_inputs = _rl_missing_training_inputs(root)
    if missing_inputs:
        return jsonify(
            {
                "status": "missing_inputs",
                "message": "RL training data files are missing on this deployment.",
                "missing_files": missing_inputs,
            }
        ), 400

    started = _rl_start_training_worker(
        episodes=n,
        use_existing=use_existing,
        max_rows=max_rows,
        training_acknowledged=training_acknowledged,
        dueling=dueling,
        prioritized=prioritized,
        trigger="manual",
    )
    if not started:
        return jsonify(
            {
                "status": "busy",
                "message": "A training run is still active on the server. Wait for it to finish (this page reloads when done), then try again.",
            }
        ), 409
    return jsonify(
        {
            "status": "training_started",
            "episodes": n,
            "max_rows": max_rows,
            "dueling": dueling,
            "prioritized": prioritized,
        }
    )


@app.route("/api/rl/production", methods=["GET"])
@login_required
def api_rl_production():
    from rl.inference import rl_paths
    from rl.production import (
        CIRCUIT_LOOKBACK_HOURS,
        CIRCUIT_MAX_DD_PCT,
        circuit_halt_buy,
        load_circuit_state,
        load_paper_trades,
    )

    root = rl_paths()["models_dir"]
    trades = load_paper_trades(root)
    return jsonify(
        {
            "circuit": load_circuit_state(root),
            "circuit_halt_buy": circuit_halt_buy(root),
            "paper_trades_count": len(trades),
            "circuit_max_drawdown_pct": CIRCUIT_MAX_DD_PCT,
            "circuit_lookback_hours": CIRCUIT_LOOKBACK_HOURS,
        }
    )


@app.route("/api/rl/circuit/reset", methods=["POST"])
@login_required
def api_rl_circuit_reset():
    from rl.inference import rl_paths
    from rl.production import reset_circuit

    root = rl_paths()["models_dir"]
    st = reset_circuit(root)
    return jsonify({"status": "ok", "circuit": st})


@app.route("/api/rl/signal", methods=["POST"])
@login_required
def api_rl_signal():
    from rl.inference import (
        apply_circuit_buy_halt,
        compute_rl_signals,
        load_agent_and_scaler,
        merge_shadow_signals,
        rl_paths,
    )
    from rl.production import append_paper_trade, circuit_halt_buy, refresh_circuit_from_paper_trades

    body = request.get_json(silent=True) or {}
    market_data = body.get("market_data") or []
    live = body.get("live_economic_event") or LIVE_ECONOMIC_EVENT
    shadow_mode = bool(body.get("shadow_mode", False))
    sim_equity = body.get("sim_equity")

    if not ai_predictor.feature_cols:
        return jsonify({"error": "Feature columns not loaded"}), 503

    root = rl_paths()["models_dir"]
    refresh_circuit_from_paper_trades(root)
    halt = circuit_halt_buy(root)

    agent, scaler = load_agent_and_scaler()
    if agent is None or scaler is None:
        return jsonify({"error": "RL agent not trained. Run training first.", "trained": False}), 400

    try:
        signals = compute_rl_signals(ai_predictor, market_data, live, agent, scaler)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not signals:
        return jsonify({"error": "No signals (check market_data)."}), 400

    signals, circ_applied = apply_circuit_buy_halt(signals, halt)

    try:
        append_paper_trade(
            {
                "endpoint": "/api/rl/signal",
                "shadow_mode": shadow_mode,
                "per_symbol": signals,
                "sim_equity": sim_equity,
            },
            base=root,
        )
        refresh_circuit_from_paper_trades(root)
    except Exception as e:
        print(f"RL paper trade log warning: {e}")

    if shadow_mode:
        merged = merge_shadow_signals(ai_predictor, market_data, live, signals)
        s0 = signals[0] if signals else {}
        return jsonify(
            {
                "action": s0.get("action", "HOLD"),
                "action_id": s0.get("action_id", 0),
                "q_values": s0.get("q_values", [0.0, 0.0, 0.0]),
                "q_gap": s0.get("q_gap", 0.0),
                "confidence": s0.get("confidence", 55.0),
                "per_symbol": signals,
                "circuit_breaker_applied": circ_applied,
                "shadow": merged,
            }
        )

    s0 = signals[0]
    return jsonify(
        {
            "action": s0["action"],
            "action_id": s0["action_id"],
            "q_values": s0["q_values"],
            "q_gap": s0.get("q_gap", 0.0),
            "confidence": s0["confidence"],
            "per_symbol": signals,
            "circuit_breaker_applied": circ_applied,
        }
    )


# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', message="Page not found"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', message="Internal server error"), 500

# ====================================================================
# FinancialPulse (financial_sentiment_v8) FULL INTEGRATION
# ====================================================================

def _integrate_financialpulse(app: Flask, socketio: SocketIO) -> None:
    """
    Integrates FinancialPulse into MarketMinds.
    - Mounts UI at /financialpulse
    - Serves FP static files at /financialpulse/static/
    - Clones all /api/* routes from FP into MarketMinds
    - Re-points FP's socketio to the MarketMinds socketio instance
      so WebSocket events (price_update, news_update) actually fire
    """
    try:
        fp = importlib.import_module(
            "financial_sentiment_v8.financial_sentiment_v8.fyp_enhanced.app"
        )
    except Exception as e:
        print(f"[FinancialPulse] Integration skipped (import failed): {e}")
        @app.route("/financialpulse")
        def financialpulse_ui_fallback():
            return render_template("error.html",
                                   message=f"FinancialPulse module could not be loaded: {e}"), 500
        return

    # ── Re-bind FinancialPulse socketio to OUR socketio ──────────────────────
    # This makes fp._full_refresh() emit events through the working socketio,
    # not through fp's own isolated SocketIO instance which has no server.
    try:
        fp.socketio = socketio
        print("[FinancialPulse] socketio re-bound to MarketMinds socketio ✓")
    except Exception as e:
        print(f"[FinancialPulse] socketio rebind warning: {e}")

    # ── Serve FinancialPulse static assets under /financialpulse/static ──────
    try:
        fp_static_dir = Path(fp.__file__).resolve().parent / "static"
        fp_static_bp = Blueprint(
            "financialpulse_static",
            __name__,
            static_folder=str(fp_static_dir),
            static_url_path="/financialpulse/static",
        )
        app.register_blueprint(fp_static_bp)
    except Exception as e:
        print(f"[FinancialPulse] Static integration warning: {e}")

    # ── Mount FinancialPulse UI page ──────────────────────────────────────────
    @app.route("/financialpulse")
    def financialpulse_ui():
        try:
            fp_templates_dir = Path(fp.__file__).resolve().parent / "templates"
            return send_from_directory(str(fp_templates_dir), "index.html")
        except Exception as e:
            return render_template("error.html", message=f"FinancialPulse UI failed to load: {e}"), 500

    # ── Log FP DB path ────────────────────────────────────────────────────────
    try:
        fp_db_path = Path(fp.__file__).resolve().parent / "financialpulse.db"
        print(f"[FinancialPulse] DB at: {fp_db_path}")
    except Exception as e:
        print(f"[FinancialPulse] DB path warning: {e}")

    # ── Clone FinancialPulse API routes into MarketMinds ─────────────────────
    routes_added = 0
    try:
        existing_rules = {r.rule for r in app.url_map.iter_rules()}
        existing_endpoints = set(app.view_functions.keys())

        for rule in fp.app.url_map.iter_rules():
            if rule.rule == "/" or rule.rule.startswith("/static"):
                continue
            if not rule.rule.startswith("/api/"):
                continue

            view_func = fp.app.view_functions.get(rule.endpoint)
            if not view_func:
                continue

            # Skip conflicts
            if rule.rule in existing_rules:
                continue

            endpoint_name = f"financialpulse__{rule.endpoint}"
            if endpoint_name in existing_endpoints:
                continue

            methods = sorted(m for m in (rule.methods or set()) if m not in {"HEAD", "OPTIONS"})
            try:
                app.add_url_rule(rule.rule, endpoint=endpoint_name, view_func=view_func, methods=methods)
                routes_added += 1
            except Exception as re:
                print(f"[FinancialPulse] Could not add route {rule.rule}: {re}")

        print(f"[FinancialPulse] {routes_added} API routes integrated under /api/*")
    except Exception as e:
        print(f"[FinancialPulse] API integration warning: {e}")

    # Release /fp-loading as soon as the FP page + APIs exist; Socket.IO wiring below can lag under eventlet workers.
    _FP_INTEGRATION_READY.set()

    # ── Register Socket.IO event handlers from FP onto our socketio ──────────
    try:
        @socketio.on('connect')
        def on_connect():
            from flask_socketio import emit
            from financial_sentiment_v8.financial_sentiment_v8.fyp_enhanced.utils.price_fetcher import get_cached_prices
            from financial_sentiment_v8.financial_sentiment_v8.fyp_enhanced.utils.news_fetcher import get_cached_news
            prices = get_cached_prices()
            if prices:
                emit('price_update', {'prices': prices})
            news = get_cached_news()
            if news:
                clean = [{k: v for k, v in n.items() if k != '_raw_text'} for n in news[:10]]
                emit('news_update', {'articles': clean})

        @socketio.on('request_refresh')
        def on_request_refresh():
            import threading
            from flask_socketio import emit
            threading.Thread(target=fp._full_refresh, daemon=True).start()
            emit('refresh_started', {'message': 'Refreshing...'})

        print("[FinancialPulse] Socket.IO event handlers registered ✓")
    except Exception as e:
        print(f"[FinancialPulse] Socket.IO handlers warning: {e}")


def _should_integrate_financialpulse_on_boot() -> bool:
    force_enable = os.environ.get("MM_ENABLE_FINANCIALPULSE_ON_BOOT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    is_railway = any(
        (os.environ.get(k) or "").strip()
        for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")
    )
    is_render = bool((os.environ.get("RENDER") or "").strip())
    return force_enable or (not is_railway and not is_render)


def _run_financialpulse_integration() -> None:
    try:
        _integrate_financialpulse(app, socketio)
    except Exception as e:
        print(f"[FinancialPulse] integration failed: {e}")
    finally:
        _FP_INTEGRATION_READY.set()


def _ensure_fp_integration_started() -> None:
    global _FP_INTEGRATION_STARTED
    if _FP_INTEGRATION_READY.is_set():
        return
    with _FP_INTEGRATION_LOCK:
        if _FP_INTEGRATION_STARTED:
            return
        _FP_INTEGRATION_STARTED = True
        threading.Thread(target=_run_financialpulse_integration, daemon=True, name="fp-integrate").start()


if _should_integrate_financialpulse_on_boot():
    _run_financialpulse_integration()
else:
    print(
        "[FinancialPulse] Boot integration disabled on this platform for low-latency startup; "
        "lazy-loading on first /financialpulse request. Set MM_ENABLE_FINANCIALPULSE_ON_BOOT=1 "
        "to force boot-time integration."
    )

_railway_env = bool(os.environ.get("RAILWAY_ENVIRONMENT", "").strip())
_rl_on_railway = os.environ.get("RL_AUTO_TRAIN_ON_RAILWAY", "").strip().lower() in ("1", "true", "yes")
_log_rl_dataset_source_status()
if not _railway_env or _rl_on_railway:
    _ensure_rl_auto_train_thread()
else:
    print("[RL auto-train] Skipped on Railway boot (set RL_AUTO_TRAIN_ON_RAILWAY=1 to enable).")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    _here = Path(__file__).resolve()
    print(f"\n{'='*55}")
    print(f"  Serving from: {_here.parent}")
    print(f"  MarketMinds.ai  →  http://localhost:{port}/dashboard")
    print(f"  FinancialPulse  →  http://localhost:{port}/financialpulse")
    print(f"  RL agent        →  http://localhost:{port}/rl_trading  (alias: /rl_agent)")
    print(f"{'='*55}\n")
    # Must use socketio.run() — NOT app.run() — for WebSockets to work
    socketio.run(app, debug=False, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)