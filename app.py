import os
import secrets
import base64
import tempfile
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file, send_from_directory, abort
import textwrap
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai
import requests
import json
from dotenv import load_dotenv
import urllib.parse
import threading
import time

from extensions import db
from models import User, ChatSession, Message
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    CAN_PDF = True
except Exception:
    CAN_PDF = False
from io import BytesIO
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

load_dotenv()

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
try:
    from flask_cors import CORS
    CORS(app)
except Exception:
    # flask-cors not installed in this environment; proceed without CORS
    pass
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'pawsense-session-secret-key-production-2026')

# Detect Vercel / serverless environment
IS_VERCEL = bool(os.getenv('VERCEL') or os.getenv('AWS_LAMBDA_FUNCTION_NAME'))

# Configure database: support Turso Cloud DB, DATABASE_URL, or /tmp for Vercel, or local sqlite
turso_url = os.getenv('TURSO_DATABASE_URL') or os.getenv('TURSO_URL')
turso_token = os.getenv('TURSO_AUTH_TOKEN')
database_url = os.getenv('DATABASE_URL')

has_turso = False
if turso_url and turso_token:
    try:
        import sqlalchemy_libsql  # Explicitly import to register 'sqlite.libsql' dialect
        has_turso = True
    except Exception as e:
        print(f"Warning: sqlalchemy_libsql could not be loaded: {e}")
        has_turso = False

if has_turso:
    turso_url = turso_url.strip()
    turso_token = turso_token.strip()
    clean_host = turso_url.replace('libsql://', '').replace('https://', '').split('/')[0].strip()
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite+libsql://{clean_host}?secure=true"
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'check_same_thread': False,
            'auth_token': turso_token,
        }
    }
elif database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    elif database_url.startswith('libsql://'):
        database_url = database_url.replace('libsql://', 'sqlite+libsql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
elif IS_VERCEL:
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'pawsense.db')}"
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pawsense.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configure folders: on Vercel / serverless, write to /tmp to avoid read-only filesystem errors
storage_base = os.path.join(tempfile.gettempdir(), 'pawsense') if IS_VERCEL else os.path.join(app.root_path, 'static')
app.config['UPLOAD_FOLDER'] = os.path.join(storage_base, 'uploads')
app.config['REPORT_FOLDER'] = os.path.join(storage_base, 'reports')
app.config['ARCHIVE_FOLDER'] = os.path.join(storage_base, 'archives')
app.config['GENERATED_RETENTION_DAYS'] = int(os.getenv('GENERATED_RETENTION_DAYS', '7'))
app.config['CLEANUP_INTERVAL_HOURS'] = int(os.getenv('CLEANUP_INTERVAL_HOURS', '24'))
app.config['ARCHIVE_RETENTION_DAYS'] = int(os.getenv('ARCHIVE_RETENTION_DAYS', '30'))

try:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['REPORT_FOLDER'], exist_ok=True)
    os.makedirs(app.config['ARCHIVE_FOLDER'], exist_ok=True)
except Exception as e:
    print(f"Warning: could not create storage directories: {e}")

db.init_app(app)

# Max allowed image size in bytes
app.config['MAX_IMAGE_MB'] = int(os.getenv('MAX_IMAGE_MB', '5'))
app.config['MAX_IMAGE_BYTES'] = app.config['MAX_IMAGE_MB'] * 1024 * 1024

# Configure Gemini and Google OAuth keys
genai_api_key = os.getenv("GOOGLE_API_KEY")
GOOGLE_SERVER_KEY = os.getenv("GOOGLE_SERVER_KEY")
GOOGLE_BROWSER_KEY = os.getenv("GOOGLE_BROWSER_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/auth/google/callback")
# alias used in code
api_key = genai_api_key
if genai_api_key:
    try:
        genai.configure(api_key=genai_api_key)
    except Exception:
        pass


def get_gemini_api_key():
    """Dynamically read the latest GOOGLE_API_KEY from .env and configure genai if changed."""
    global api_key, genai_api_key
    try:
        load_dotenv(override=True)
    except Exception:
        pass
    k = os.getenv("GOOGLE_API_KEY")
    if k:
        k = k.strip().strip("'\"")
    if k and k != genai_api_key:
        genai_api_key = k
        api_key = k
        try:
            genai.configure(api_key=k)
        except Exception as e:
            print(f"Warning: genai.configure failed with key: {e}")
    return k or genai_api_key or api_key


with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Warning: db.create_all() failed: {e}")
    # Ensure DB columns added by recent model changes exist (simple runtime migration)
    try:
        from sqlalchemy import text
        conn = db.engine.connect()
        try:
            res = conn.execute(text("PRAGMA table_info('message')"))
            cols = [r._mapping['name'] if hasattr(r, '_mapping') and 'name' in r._mapping else (r['name'] if 'name' in r else None) for r in res]
        except Exception:
            cols = []
        if 'archived' not in cols:
            try:
                conn.execute(text("ALTER TABLE message ADD COLUMN archived INTEGER DEFAULT 0"))
            except Exception:
                pass
        if 'archive_path' not in cols:
            try:
                conn.execute(text("ALTER TABLE message ADD COLUMN archive_path VARCHAR(255)"))
            except Exception:
                pass
        if 'structured' not in cols:
            try:
                conn.execute(text("ALTER TABLE message ADD COLUMN structured TEXT"))
            except Exception:
                pass
        if 'image_caption' not in cols:
            try:
                conn.execute(text("ALTER TABLE message ADD COLUMN image_caption VARCHAR(255)"))
            except Exception:
                pass
        if 'image_paths' not in cols:
            try:
                conn.execute(text("ALTER TABLE message ADD COLUMN image_paths TEXT"))
            except Exception:
                pass
        if 'image_captions' not in cols:
            try:
                conn.execute(text("ALTER TABLE message ADD COLUMN image_captions TEXT"))
            except Exception:
                pass
        conn.close()
    except Exception:
        pass


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in kilometers between two lat/lon coordinates."""
    import math
    try:
        r = 6371.0
        dlat = math.radians(float(lat2) - float(lat1))
        dlon = math.radians(float(lon2) - float(lon1))
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(r * c, 2)
    except Exception:
        return None


def get_nearby_hospitals(lat, lon):
    """Query Google Places or OpenStreetMap for veterinary hospitals near lat,lon.
    Returns a list of dicts with name, address, lat, lng, rating, distance_km, and navigation links.
    """
    key = GOOGLE_SERVER_KEY or genai_api_key or os.getenv("GOOGLE_API_KEY")
    results = []

    # 1. Try Google Places Nearby Search if key is available
    if key:
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {
            'location': f"{lat},{lon}",
            'rankby': 'distance',
            'type': 'veterinary_care',
            'key': key
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            for r in data.get('results', [])[:10]:
                r_lat = r.get('geometry', {}).get('location', {}).get('lat')
                r_lng = r.get('geometry', {}).get('location', {}).get('lng')
                q_name = urllib.parse.quote(str(r.get('name') or ''))
                q_addr = urllib.parse.quote(str(r.get('vicinity') or ''))
                results.append({
                    'name': r.get('name'),
                    'address': r.get('vicinity') or '',
                    'lat': r_lat,
                    'lng': r_lng,
                    'rating': r.get('rating'),
                    'distance_km': haversine_distance(lat, lon, r_lat, r_lng) if (r_lat and r_lng) else None,
                    'directions_url': f"https://www.google.com/maps/dir/?api=1&destination={r_lat},{r_lng}" if (r_lat and r_lng) else None,
                    'maps_url': f"https://www.google.com/maps/search/?api=1&query={q_name}+{q_addr}"
                })
            
            # Fallback to hospital+keyword if no veterinary_care results
            if not results and data.get('status') == 'OK':
                params2 = {
                    'location': f"{lat},{lon}",
                    'rankby': 'distance',
                    'type': 'hospital',
                    'keyword': 'veterinary',
                    'key': key
                }
                resp2 = requests.get(url, params=params2, timeout=5)
                data2 = resp2.json()
                for r in data2.get('results', [])[:10]:
                    r_lat = r.get('geometry', {}).get('location', {}).get('lat')
                    r_lng = r.get('geometry', {}).get('location', {}).get('lng')
                    q_name = urllib.parse.quote(str(r.get('name') or ''))
                    q_addr = urllib.parse.quote(str(r.get('vicinity') or ''))
                    results.append({
                        'name': r.get('name'),
                        'address': r.get('vicinity') or '',
                        'lat': r_lat,
                        'lng': r_lng,
                        'rating': r.get('rating'),
                        'distance_km': haversine_distance(lat, lon, r_lat, r_lng) if (r_lat and r_lng) else None,
                        'directions_url': f"https://www.google.com/maps/dir/?api=1&destination={r_lat},{r_lng}" if (r_lat and r_lng) else None,
                        'maps_url': f"https://www.google.com/maps/search/?api=1&query={q_name}+{q_addr}"
                    })
        except Exception:
            pass

    # 2. Free OpenStreetMap Overpass API fallback if Google Places returned no results or is unavailable/unauthorized
    post_fn = getattr(requests, 'post', None)
    if not results and callable(post_fn):
        try:
            osm_query = f"""
            [out:json][timeout:8];
            (
              node["amenity"="veterinary"](around:20000, {lat}, {lon});
              way["amenity"="veterinary"](around:20000, {lat}, {lon});
            );
            out center 12;
            """
            headers = {"User-Agent": "PawSense-App/1.0 (veterinary-locator)"}
            resp = post_fn("https://overpass-api.de/api/interpreter", data={"data": osm_query}, headers=headers, timeout=8)
            if resp.status_code == 200:
                osm_data = resp.json()
                for el in osm_data.get('elements', []):
                    tags = el.get('tags', {})
                    r_lat = el.get('lat') or (el.get('center') or {}).get('lat')
                    r_lng = el.get('lon') or (el.get('center') or {}).get('lon')
                    name = tags.get('name') or tags.get('name:en') or tags.get('operator') or 'Veterinary Clinic'
                    addr_parts = [tags.get(k) for k in ['addr:housenumber', 'addr:street', 'addr:suburb', 'addr:city'] if tags.get(k)]
                    address = ', '.join(addr_parts) if addr_parts else tags.get('address') or ''
                    phone = tags.get('phone') or tags.get('contact:phone') or None
                    q_name = urllib.parse.quote(name)
                    q_addr = urllib.parse.quote(address)
                    results.append({
                        'name': name,
                        'address': address,
                        'phone': phone,
                        'lat': r_lat,
                        'lng': r_lng,
                        'rating': None,
                        'distance_km': haversine_distance(lat, lon, r_lat, r_lng) if (r_lat and r_lng) else None,
                        'directions_url': f"https://www.google.com/maps/dir/?api=1&destination={r_lat},{r_lng}" if (r_lat and r_lng) else None,
                        'maps_url': f"https://www.google.com/maps/search/?api=1&query={q_name}+{q_addr}"
                    })
                # Sort by distance
                results.sort(key=lambda x: x.get('distance_km') or 999999)
                results = results[:10]
        except Exception:
            pass

    return results


def generate_svg_data_url(text, title='Diet Plan'):
    """Create a simple SVG image containing the text (wrapped) and return (data_url, svg_string)."""
    lines = textwrap.wrap(text, 60)
    width = 600
    line_height = 18
    padding = 20
    height = padding * 2 + max(80, line_height * len(lines) + 30)
    svg_lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    svg_lines.append(f'<rect width="100%" height="100%" fill="#fff7ed" rx="18"/>')
    svg_lines.append(f'<style>text{{font-family:Inter,Arial,Helvetica,sans-serif;fill:#0f172a}}</style>')
    svg_lines.append(f'<text x="{padding}" y="{padding + 16}" font-size="18" font-weight="700">{title}</text>')
    y = padding + 16 + 8
    svg_lines.append(f'<g transform="translate(0,8)">')
    for i, line in enumerate(lines):
        yy = y + (i+1) * line_height
        svg_lines.append(f'<text x="{padding}" y="{yy}" font-size="14">{line}</text>')
    svg_lines.append('</g>')
    svg_lines.append('</svg>')
    svg = '\n'.join(svg_lines)
    data_url = 'data:image/svg+xml;utf8,' + urllib.parse.quote(svg)
    return data_url, svg


def normalize_username(value):
    if value is None:
        return ''
    return str(value).strip().lower()


def extract_model_response_text(response):
    """Normalize Gemini responses into readable text and provide a user-safe fallback."""
    if response is None:
        return "I couldn’t generate a reply for that request. Please try rephrasing your question."

    text = getattr(response, 'text', None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    try:
        if hasattr(response, 'candidates'):
            for candidate in response.candidates:
                parts = getattr(candidate, 'content', None)
                if not parts:
                    continue
                for part in getattr(parts, 'parts', []) or []:
                    if getattr(part, 'text', None):
                        return part.text.strip()
    except Exception:
        pass

    feedback = getattr(response, 'prompt_feedback', None)
    if feedback is not None:
        block_reason = getattr(feedback, 'block_reason', None)
        if block_reason is not None:
            return "I can’t answer that request because the AI blocked it for safety reasons. Please try rephrasing it more simply."

    return "I’m ready to help, but I didn’t receive a meaningful reply from the AI. Please try again or rephrase your question."


@app.errorhandler(404)
def not_found(e):
    if request.path in ('/api/index', '/api/index.py', '/api'):
        return redirect(url_for('index'))
    if 'user_id' not in session:
        return render_template('login.html'), 200
    return redirect(url_for('index'))


@app.errorhandler(500)
def server_error(e):
    import traceback
    err_tb = traceback.format_exc()
    print("PawSense 500 Error Traceback:\n", err_tb)
    session.pop('user_id', None)
    err_msg = str(e)
    if err_tb:
        lines = [l.strip() for l in err_tb.strip().split('\n') if l.strip()]
        if lines:
            err_msg = lines[-1]
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error', 'details': err_msg}), 500
    return render_template('login.html', error=f"Server error: {err_msg}"), 200


@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        user = User.query.get(session['user_id'])
    except Exception as e:
        print(f"Error fetching user from database: {e}")
        session.pop('user_id', None)
        return redirect(url_for('login'))

    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))

    # Pass the browser-restricted key to the frontend for Maps JS (if present)
    return render_template('index.html', user=user, google_browser_key=GOOGLE_BROWSER_KEY or '')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = normalize_username(request.form.get('username'))
        password = request.form.get('password')

        if not username:
            return render_template('signup.html', error="Username is required")

        if User.query.filter(func.lower(User.username) == username).first():
            return render_template('signup.html', error="Username already exists")

        new_user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(new_user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return render_template('signup.html', error="Username already exists")
        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/auth/google/login')
def google_oauth_login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return render_template('login.html', error='Google login is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file.')

    state = secrets.token_urlsafe(16)
    session['google_oauth_state'] = state
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'access_type': 'offline',
        'prompt': 'select_account',
        'state': state,
    }
    google_auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    return redirect(google_auth_url)


@app.route('/auth/google/callback')
def google_oauth_callback():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return render_template('login.html', error='Google login is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file.')

    error = request.args.get('error')
    if error:
        return render_template('login.html', error='Google login was cancelled or denied.')

    code = request.args.get('code')
    if not code:
        return render_template('login.html', error='Google login did not return an authorization code.')

    expected_state = session.pop('google_oauth_state', None)
    if request.args.get('state') and expected_state and request.args.get('state') != expected_state:
        return render_template('login.html', error='Google login state validation failed.')

    token_response = requests.post(
        'https://oauth2.googleapis.com/token',
        data={
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code',
        },
        timeout=30,
    )
    token_data = token_response.json() if token_response.headers.get('Content-Type', '').startswith('application/json') else {}
    access_token = token_data.get('access_token')
    if not access_token:
        return render_template('login.html', error='Google login failed during token exchange.')

    userinfo_response = requests.get(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=30,
    )
    userinfo = userinfo_response.json() if userinfo_response.headers.get('Content-Type', '').startswith('application/json') else {}
    email = (userinfo.get('email') or userinfo.get('name') or '').strip()
    if not email:
        return render_template('login.html', error='Google login did not return a valid email address.')

    username = normalize_username(email)
    user = User.query.filter(func.lower(User.username) == username).first()
    if not user:
        user = User(username=username, password_hash=generate_password_hash(f"google-oauth::{secrets.token_urlsafe(32)}"))
        db.session.add(user)
    db.session.commit()
    session['user_id'] = user.id
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = normalize_username(request.form.get('username'))
        password = request.form.get('password')

        user = User.query.filter(func.lower(User.username) == username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('index'))

        if not user:
            return render_template('login.html', error="Account not found. Please click 'Sign up here' below to create your account.")
        return render_template('login.html', error="Invalid password. Please try again.")

    return render_template('login.html', error=request.args.get('error'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/mock_social_login/<provider>', methods=['GET', 'POST'])
def mock_social_login(provider):
    if request.method == 'POST':
        username = normalize_username(request.form.get('username'))
        password = request.form.get('password')

        if not username:
            return render_template('social_auth.html', provider=provider, error='Username is required')

        user = User.query.filter(func.lower(User.username) == username).first()

        if user:
            if check_password_hash(user.password_hash, password):
                session['user_id'] = user.id
                return redirect(url_for('index'))
            return render_template('social_auth.html', provider=provider, error="Invalid password")

        new_user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(new_user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing_user = User.query.filter(func.lower(User.username) == username).first()
            if existing_user:
                if check_password_hash(existing_user.password_hash, password):
                    session['user_id'] = existing_user.id
                    return redirect(url_for('index'))
                return render_template('social_auth.html', provider=provider, error="Invalid password")
            return render_template('social_auth.html', provider=provider, error="This account already exists")
        session['user_id'] = new_user.id
        return redirect(url_for('index'))

    return render_template('social_auth.html', provider=provider)

@app.route('/api/debug-db', methods=['GET'])
def debug_db():
    import sys
    info = {
        'turso_url_set': bool(os.getenv('TURSO_DATABASE_URL') or os.getenv('TURSO_URL')),
        'turso_token_set': bool(os.getenv('TURSO_AUTH_TOKEN')),
        'is_vercel': IS_VERCEL,
        'has_turso': has_turso,
        'db_uri_prefix': str(app.config.get('SQLALCHEMY_DATABASE_URI', ''))[:40],
        'python_version': sys.version,
    }
    try:
        import sqlalchemy_libsql
        info['sqlalchemy_libsql_imported'] = True
    except Exception as e:
        info['sqlalchemy_libsql_imported'] = False
        info['sqlalchemy_libsql_error'] = str(e)

    try:
        u_count = User.query.count()
        info['user_count'] = u_count
        info['db_query_ok'] = True
    except Exception as e:
        info['db_query_ok'] = False
        info['db_query_error'] = str(e)

    return jsonify(info)

@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    sessions = ChatSession.query.filter_by(user_id=session['user_id']).order_by(ChatSession.created_at.desc()).all()
    return jsonify([
        {
            'id': s.id,
            'title': s.title,
            'created_at': s.created_at.isoformat()
        } for s in sessions
    ])

@app.route('/api/sessions', methods=['POST'])
def create_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    new_session = ChatSession(user_id=session['user_id'], title="New Conversation")
    db.session.add(new_session)
    db.session.commit()
    return jsonify({
        'id': new_session.id,
        'title': new_session.title,
        'created_at': new_session.created_at.isoformat()
    })

@app.route('/api/sessions', methods=['DELETE'])
def clear_sessions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        sessions = ChatSession.query.filter_by(user_id=session['user_id']).all()
        for s in sessions:
            Message.query.filter_by(session_id=s.id).delete()
            db.session.delete(s)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/sessions/<int:session_id>', methods=['DELETE'])
def delete_session(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    session_obj = ChatSession.query.get(session_id)
    if not session_obj or session_obj.user_id != session['user_id']:
        return jsonify({'error': 'Session not found or unauthorized'}), 404
    try:
        Message.query.filter_by(session_id=session_id).delete()
        db.session.delete(session_obj)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/sessions/<int:session_id>/messages', methods=['GET'])
def get_messages(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    session_obj = ChatSession.query.get(session_id)
    if not session_obj or session_obj.user_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 401
    messages = Message.query.filter_by(session_id=session_id).order_by(Message.created_at.asc()).all()
    out = []
    for m in messages:
        structured_obj = None
        if getattr(m, 'structured', None):
            try:
                structured_obj = json.loads(m.structured)
            except Exception:
                structured_obj = None
        out.append({
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'image_path': m.image_path,
            'image_caption': getattr(m, 'image_caption', None),
            'image_paths': (json.loads(m.image_paths) if getattr(m, 'image_paths', None) else None),
            'image_captions': (json.loads(m.image_captions) if getattr(m, 'image_captions', None) else None),
            'structured': structured_obj,
            'created_at': m.created_at.isoformat()
        })
    return jsonify(out)

@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio_route():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    audio_file = request.files.get('audio')
    if not audio_file or not getattr(audio_file, 'filename', None):
        return jsonify({'error': 'Audio file is required'}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify({'error': 'Audio file is empty'}), 400

    mime_type = (audio_file.mimetype or request.mimetype or 'audio/webm').lower()
    if ';' in mime_type:
        mime_type = mime_type.split(';')[0].strip()

    # 1. Primary: Gemini Multimodal audio transcription (uses user's GOOGLE_API_KEY from AI Studio)
    active_key = get_gemini_api_key()
    if active_key:
        try:
            for m_name in ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"]:
                try:
                    m = genai.GenerativeModel(m_name)
                    prompt = (
                        "Transcribe the spoken words in this audio clip into clean plain text for a pet assistant message. "
                        "Return ONLY the exact transcribed text, nothing else."
                    )
                    resp = m.generate_content([
                        prompt,
                        {"mime_type": mime_type if mime_type else "audio/webm", "data": audio_bytes}
                    ])
                    transcript = resp.text.strip() if (resp and resp.text) else ""
                    if transcript:
                        return jsonify({'transcript': transcript})
                except Exception as m_err:
                    print(f"Gemini audio transcription with {m_name} failed: {m_err}")
                    continue
        except Exception as e:
            print(f"Gemini audio transcription error: {e}")

    # 2. Secondary fallback: Google Cloud Speech API (if GOOGLE_SERVER_KEY is explicitly set)
    if GOOGLE_SERVER_KEY:
        try:
            encoding_map = {
                'audio/webm': 'WEBM_OPUS',
                'audio/ogg': 'OGG_OPUS',
                'audio/mpeg': 'MP3',
                'audio/mp3': 'MP3',
                'audio/mp4': 'MP3',
                'audio/wav': 'LINEAR16',
                'audio/x-wav': 'LINEAR16'
            }
            encoding = encoding_map.get(mime_type, 'WEBM_OPUS')
            sample_rate = 48000 if encoding in {'WEBM_OPUS', 'OGG_OPUS'} else 44100 if encoding == 'MP3' else 16000
            payload = {
                'config': {
                    'encoding': encoding,
                    'sampleRateHertz': sample_rate,
                    'languageCode': 'en-US',
                    'enableAutomaticPunctuation': True,
                },
                'audio': {'content': base64.b64encode(audio_bytes).decode('utf-8')}
            }
            response = requests.post(
                'https://speech.googleapis.com/v1/speech:recognize',
                params={'key': GOOGLE_SERVER_KEY},
                json=payload,
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                for result in data.get('results', []):
                    alternatives = result.get('alternatives', [])
                    if alternatives:
                        transcript = alternatives[0].get('transcript', '').strip()
                        if transcript:
                            return jsonify({'transcript': transcript})
        except Exception as exc:
            print(f'Google Cloud Speech API failed: {exc}')

    return jsonify({'error': 'No speech recognized. Please speak into the microphone or type your message.'}), 422


@app.route('/api/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    session_id = request.form.get('session_id')
    content = request.form.get('content', '')
    
    if not session_id:
        return jsonify({'error': 'Session ID is required'}), 400
        
    chat_session = ChatSession.query.get(session_id)
    if not chat_session or chat_session.user_id != session['user_id']:
        return jsonify({'error': 'Session not found or unauthorized'}), 404

    # Support multiple image uploads (fields named 'image')
    uploaded_files = request.files.getlist('image') if hasattr(request.files, 'getlist') else ([request.files.get('image')] if request.files.get('image') else [])
    # Gather captions sent as repeated 'image_caption' fields
    captions_list = request.form.getlist('image_caption') if request.form else []

    # Validate files before saving
    allowed_exts = {'jpg', 'jpeg', 'png', 'webp'}
    max_bytes = app.config.get('MAX_IMAGE_BYTES', 5 * 1024 * 1024)
    for f in uploaded_files:
        if not f or not getattr(f, 'filename', None):
            continue
        filename = secure_filename(f.filename)
        if '.' not in filename:
            return jsonify({'error': 'File missing extension'}), 400
        ext = filename.rsplit('.', 1)[1].lower()
        if ext not in allowed_exts:
            return jsonify({'error': f'Invalid file type: {ext}. Allowed: jpg,jpeg,png,webp'}), 400
        # check size (seek to end)
        try:
            f.stream.seek(0, os.SEEK_END)
            size = f.stream.tell()
            f.stream.seek(0)
        except Exception:
            # fallback: attempt reading content length
            size = 0
        if size and size > max_bytes:
            return jsonify({'error': f'File too large: {round(size/1024/1024,2)}MB. Max {app.config.get("MAX_IMAGE_MB") }MB'}), 400
    image_path = None
    image_caption = request.form.get('image_caption')
    saved_paths = []
    if uploaded_files:
        for image_file in uploaded_files:
            if not image_file or image_file.filename == '':
                continue
            filename = secure_filename(image_file.filename)
            unique_filename = f"{secrets.token_hex(8)}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            image_file.save(file_path)
            saved_paths.append(f"static/uploads/{unique_filename}")
        if saved_paths:
            image_path = saved_paths[0]
            # Attach per-image captions if provided; align lengths
            image_captions_to_store = []
            try:
                if captions_list and len(captions_list) == len(saved_paths):
                    image_captions_to_store = captions_list
                elif captions_list and len(captions_list) == 1:
                    image_captions_to_store = [captions_list[0] for _ in saved_paths]
                else:
                    image_captions_to_store = ['' for _ in saved_paths]
            except Exception:
                image_captions_to_store = ['' for _ in saved_paths]
        
    # Save user message (persist legacy single-image fields and new multi-image JSON)
    user_msg = Message(session_id=chat_session.id, role='user', content=content, image_path=image_path, image_caption=image_caption)
    if saved_paths:
        try:
            user_msg.image_paths = json.dumps(saved_paths, ensure_ascii=False)
            user_msg.image_captions = json.dumps(image_captions_to_store, ensure_ascii=False)
        except Exception:
            pass
    db.session.add(user_msg)
    
    # Update session title if it's the first message
    if chat_session.title == "New Conversation" and content:
        chat_session.title = content[:30] + "..." if len(content) > 30 else content

    db.session.commit()

    # Get language and instructions
    language = request.form.get('language', 'English')
    custom_instructions = request.form.get('custom_instructions', '')
    
    # System Instruction
    system_instruction = f"You are Pawsense, a premium AI petcare expert. You must provide helpful, kind, and professional advice about pets. \n\nCRITICAL: The user has selected {language}. You MUST respond exclusively in {language}. Do not use English if the language is Hindi or Kannada. Translate all technical terms into {language} where possible.\n\n"
    if custom_instructions:
        system_instruction += f"USER PREFERENCES: {custom_instructions}\n\n"

    # Generate response with Gemini
    response_content = ""
    active_key = get_gemini_api_key()
    try:
        if not active_key:
            response_content = "To use the AI, please add a GOOGLE_API_KEY to your .env file."
        else:
            prompt_parts = [system_instruction + content]
            if image_path:
                import PIL.Image
                img = PIL.Image.open(os.path.join(app.root_path, image_path))
                prompt_parts.append(img)

            model_candidates = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
            response = None
            last_err = None
            for m_name in model_candidates:
                try:
                    model = genai.GenerativeModel(m_name)
                    response = model.generate_content(prompt_parts)
                    break
                except Exception as m_err:
                    last_err = m_err
                    err_str = str(m_err)
                    if "leaked" in err_str.lower() or "PERMISSION_DENIED" in err_str or "API_KEY_INVALID" in err_str:
                        raise m_err
                    continue

            if response is not None:
                response_content = extract_model_response_text(response)
            elif last_err:
                raise last_err
    except Exception as e:
        print(f"Error generating response: {e}")
        err_str = str(e)
        if "leaked" in err_str.lower():
            response_content = "⚠️ Google API Error: Your API key was reported as leaked/revoked by Google. Please generate a fresh key from Google AI Studio (https://aistudio.google.com/app/apikey) and update your .env file."
        elif "API_KEY_INVALID" in err_str:
            response_content = "⚠️ Google API Error: The GOOGLE_API_KEY in your .env file is invalid. Please verify it at https://aistudio.google.com/app/apikey."
        else:
            try:
                available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                response_content = f"API Error: {str(e)}\n\nAvailable models: {', '.join(available_models)}"
            except Exception:
                response_content = f"I'm sorry, I encountered an error: {str(e)}"

    # Save model message
    model_msg = Message(session_id=chat_session.id, role='model', content=response_content)
    db.session.add(model_msg)
    db.session.commit()

    # Attempt to parse structured JSON from the model's text and persist it
    parsed_structured = None
    try:
        if response_content:
            try:
                parsed_structured = json.loads(response_content)
            except Exception:
                # try to extract first JSON object in text
                try:
                    start = response_content.find('{')
                    end = response_content.rfind('}')
                    if start != -1 and end != -1 and end > start:
                        parsed_structured = json.loads(response_content[start:end+1])
                except Exception:
                    parsed_structured = None
        if parsed_structured:
            try:
                model_msg.structured = json.dumps(parsed_structured, ensure_ascii=False)
                db.session.commit()
            except Exception:
                db.session.rollback()
    except Exception:
        parsed_structured = None

    # If this was a diet-related query, generate a simple image (SVG data URL) to show as illustration
    model_image_data = None
    model_image_path = None
    model_thumb_path = None
    try:
        if 'diet' in (content or '').lower() or 'diet' in (response_content or '').lower() or 'feeding' in (content or '').lower():
            # Generate a plate-style SVG that visualizes a balanced meal and includes a brief summary
            model_image_data, svg_text = generate_plate_svg(response_content or 'Balanced diet', title='Diet Plan')
            # Save SVG to uploads so it can be shared/downloaded
            try:
                gen_name = f"generated_{secrets.token_hex(8)}.svg"
                gen_path = os.path.join(app.config['UPLOAD_FOLDER'], gen_name)
                with open(gen_path, 'w', encoding='utf-8') as gf:
                    gf.write(svg_text)
                # Try to rasterize to PNG for wider compatibility
                try:
                    import cairosvg
                    png_name = gen_name.replace('.svg', '.png')
                    png_path = os.path.join(app.config['UPLOAD_FOLDER'], png_name)
                    cairosvg.svg2png(bytestring=svg_text.encode('utf-8'), write_to=png_path)
                    model_image_path = f"protected_uploads/{png_name}"
                    # create a thumbnail WebP for faster previews
                    try:
                        from PIL import Image
                        thumb_name = png_name.replace('.png', '_thumb.webp')
                        thumb_path = os.path.join(app.config['UPLOAD_FOLDER'], thumb_name)
                        im = Image.open(png_path)
                        im.thumbnail((480, 480))
                        im.save(thumb_path, format='WEBP', quality=75)
                        model_thumb_path = f"protected_uploads/{thumb_name}"
                    except Exception:
                        model_thumb_path = None
                    # persist ownership to the model message if available
                    try:
                        model_msg.image_path = model_image_path
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                except Exception:
                    # Fallback to SVG if rasterization fails
                    model_image_path = f"protected_uploads/{gen_name}"
                    model_thumb_path = None
                    try:
                        model_msg.image_path = model_image_path
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
            except Exception:
                model_image_path = None
    except Exception:
        model_image_data = None
        model_image_path = None

    return jsonify({
        'user_message': {
            'id': user_msg.id,
            'role': user_msg.role,
            'content': user_msg.content,
            'image_path': user_msg.image_path,
            'created_at': user_msg.created_at.isoformat()
        },
        'model_message': {
            'id': model_msg.id,
            'role': model_msg.role,
            'content': model_msg.content,
            'created_at': model_msg.created_at.isoformat(),
            'image_data': model_image_data,
            'image_path': model_image_path,
            'thumb_path': model_thumb_path
        }
    })


# NOTE: `/api/generate_structured` endpoint removed — client always posts to `/api/chat` now.


@app.route('/protected_uploads/<path:filename>')
def protected_uploads(filename):
    # Serve generated uploads only to authenticated users and owners
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    # Lookup the message that owns this generated file
    expected = f"protected_uploads/{filename}"
    owner_msg = Message.query.filter_by(image_path=expected).first()
    if not owner_msg:
        return abort(404)
    # Verify the requesting user owns the session containing the message
    if owner_msg.session.user_id != session.get('user_id'):
        return jsonify({'error': 'Forbidden'}), 403
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.isfile(safe_path):
        return abort(404)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/api/my-images')
def api_my_images():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    uid = session.get('user_id')
    images = []
    try:
        msgs = Message.query.join(ChatSession).filter(ChatSession.user_id == uid, Message.image_path != None).order_by(Message.created_at.desc()).all()
        for m in msgs:
            # image_path stored as protected_uploads/<name>
            ip = m.image_path
            if not ip:
                continue
            fname = ip.split('/')[-1]
            # derive thumb name for png files
            thumb = None
            if fname.lower().endswith('.png'):
                tname = fname.replace('.png', '_thumb.webp')
                if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], tname)):
                    thumb = f"protected_uploads/{tname}"
            # also include additional image paths if present
            multi = []
            try:
                if getattr(m, 'image_paths', None):
                    multi = json.loads(m.image_paths)
            except Exception:
                multi = []
            images.append({
                'message_id': m.id,
                'filename': fname,
                'image_path': m.image_path,
                'thumb_path': thumb,
                'image_caption': getattr(m, 'image_caption', None),
                'image_paths': multi,
                'created_at': m.created_at.isoformat()
            })
    except Exception:
        return jsonify({'error': 'Failed to list images'}), 500
    return jsonify({'images': images})


@app.route('/api/delete-generated', methods=['POST'])
def api_delete_generated():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    fname = data.get('filename')
    msg_id = data.get('message_id')
    if not fname and not msg_id:
        return jsonify({'error': 'filename or message_id required'}), 400
    try:
        if msg_id:
            msg = Message.query.get(msg_id)
        else:
            # find message by filename
            expected = f"protected_uploads/{fname}"
            msg = Message.query.filter_by(image_path=expected).first()
        if not msg:
            return jsonify({'error': 'Not found'}), 404
        if msg.session.user_id != session.get('user_id'):
            return jsonify({'error': 'Forbidden'}), 403
        # delete files: image and possible thumb
        fn = msg.image_path.split('/')[-1]
        candidates = [fn]
        if fn.lower().endswith('.png'):
            candidates.append(fn.replace('.png', '_thumb.webp'))
        if fn.lower().endswith('.svg'):
            # maybe a png sibling exists
            png = fn.replace('.svg', '.png')
            candidates.append(png)
            candidates.append(png.replace('.png', '_thumb.webp'))
        deleted = []
        for c in candidates:
            p = os.path.join(app.config['UPLOAD_FOLDER'], c)
            if os.path.isfile(p):
                try:
                    # move to archive folder instead of permanent delete
                    target = os.path.join(app.config['ARCHIVE_FOLDER'], c)
                    os.replace(p, target)
                    deleted.append(c)
                except Exception:
                    pass
        # mark message archived and store archive path
        try:
            msg.archived = True
            msg.archive_path = f"archives/{deleted[0]}" if deleted else None
            msg.image_path = None
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({'archived': deleted})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete', 'detail': str(e)}), 500


    @app.route('/api/restore-generated', methods=['POST'])
    def api_restore_generated():
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        data = request.get_json() or {}
        fname = data.get('filename')
        msg_id = data.get('message_id')
        if not fname and not msg_id:
            return jsonify({'error': 'filename or message_id required'}), 400
        try:
            if msg_id:
                msg = Message.query.get(msg_id)
            else:
                expected = f"archives/{fname}"
                msg = Message.query.filter_by(archive_path=expected).first()
            if not msg:
                return jsonify({'error': 'Not found'}), 404
            if msg.session.user_id != session.get('user_id'):
                return jsonify({'error': 'Forbidden'}), 403
            # move back files from archive
            a_fn = msg.archive_path.split('/')[-1] if msg.archive_path else None
            if not a_fn:
                return jsonify({'error':'No archive path'}), 400
            archived_full = os.path.join(app.config['ARCHIVE_FOLDER'], a_fn)
            restored = []
            if os.path.isfile(archived_full):
                target = os.path.join(app.config['UPLOAD_FOLDER'], a_fn)
                os.replace(archived_full, target)
                restored.append(a_fn)
            if restored:
                msg.archived = False
                msg.archive_path = None
                msg.image_path = f"protected_uploads/{restored[0]}"
                db.session.commit()
                return jsonify({'restored': restored})
            return jsonify({'error': 'Nothing restored'}), 400
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': 'Failed to restore', 'detail': str(e)}), 500


    @app.route('/api/message/report_pdf/<int:message_id>')
    def message_report_pdf(message_id):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        msg = Message.query.get(message_id)
        if not msg:
            return jsonify({'error':'Not found'}), 404
        if msg.session.user_id != session.get('user_id'):
            return jsonify({'error':'Forbidden'}), 403
        # find file: prefer image_path, else archive_path
        ip = msg.image_path or msg.archive_path
        if not ip:
            return jsonify({'error':'No image attached'}), 400
        fname = ip.split('/')[-1]
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        if not os.path.isfile(file_path):
            # maybe archived
            file_path = os.path.join(app.config['ARCHIVE_FOLDER'], fname)
            if not os.path.isfile(file_path):
                return jsonify({'error':'File missing'}), 404

        try:
            img_reader = ImageReader(file_path)
            bio = BytesIO()
            c = canvas.Canvas(bio, pagesize=letter)
            width, height = letter
            # Draw background
            c.setFillColorRGB(0.97,0.95,0.92)
            c.rect(0,0,width,height,fill=1,stroke=0)
            # Title
            c.setFont('Helvetica-Bold', 20)
            c.setFillColorRGB(0.07,0.12,0.2)
            c.drawString(40, height-60, 'PawSense Report')
            # Message content
            c.setFont('Helvetica', 12)
            text = msg.content or ''
            text_y = height - 90
            for line in textwrap.wrap(text, 80):
                c.drawString(40, text_y, line)
                text_y -= 14
                if text_y < 160:
                    break
            # Include image caption if present
            if getattr(msg, 'image_caption', None):
                caption_lines = textwrap.wrap('Caption: ' + (msg.image_caption or ''), 80)
                caption_y = text_y - 12
                c.setFont('Helvetica-Oblique', 11)
                for cl in caption_lines:
                    c.drawString(40, caption_y, cl)
                    caption_y -= 12
                    if caption_y < 140:
                        break
            # Draw image on the right
            img_w = 300
            img_h = 300
            img_x = width - img_w - 40
            img_y = height - img_h - 120
            c.drawImage(img_reader, img_x, img_y, width=img_w, height=img_h, preserveAspectRatio=True)
            # Footer
            c.setFont('Helvetica-Oblique', 9)
            c.drawString(40, 30, f'Report generated by PawSense — {datetime.utcnow().isoformat()}')
            c.showPage()
            c.save()
            bio.seek(0)
            return send_file(bio, mimetype='application/pdf', as_attachment=True, download_name=f'pawsense-report-{message_id}.pdf')
        except Exception as e:
            return jsonify({'error':'Failed to generate PDF', 'detail': str(e)}), 500


def cleanup_generated_files():
    """Remove generated_ files older than retention and clear DB references."""
    folder = app.config['UPLOAD_FOLDER']
    retention_seconds = app.config['GENERATED_RETENTION_DAYS'] * 24 * 3600
    now = time.time()
    deleted = []
    if not CAN_PDF:
        return jsonify({'error': 'PDF generation dependency not installed (reportlab)'}), 501
    try:
        for fn in os.listdir(folder):
            if not fn.startswith('generated_'):
                continue
            path = os.path.join(folder, fn)
            try:
                mtime = os.path.getmtime(path)
                if now - mtime > retention_seconds:
                    try:
                        os.remove(path)
                        deleted.append(fn)
                        # clear any Message.image_path referencing this file
                        try:
                            expected = f"protected_uploads/{fn}"
                            msg = Message.query.filter_by(image_path=expected).first()
                            if msg:
                                msg.image_path = None
                                db.session.commit()
                        except Exception:
                            db.session.rollback()
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass
    # Also clean archived files older than archive retention
    archive_deleted = []
    try:
        a_folder = app.config['ARCHIVE_FOLDER']
        a_retention = app.config['ARCHIVE_RETENTION_DAYS'] * 24 * 3600
        for af in os.listdir(a_folder):
            if not af.startswith('generated_'):
                continue
            a_path = os.path.join(a_folder, af)
            try:
                mtime = os.path.getmtime(a_path)
                if now - mtime > a_retention:
                    try:
                        os.remove(a_path)
                        archive_deleted.append(af)
                        # clear any Message.archive_path referencing this file
                        try:
                            expected = f"archives/{af}"
                            msg = Message.query.filter_by(archive_path=expected).first()
                            if msg:
                                msg.archive_path = None
                                msg.archived = False
                                db.session.commit()
                        except Exception:
                            db.session.rollback()
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass

    return {'deleted': deleted, 'archive_deleted': archive_deleted}


def _cleanup_worker():
    interval = app.config['CLEANUP_INTERVAL_HOURS'] * 3600
    while True:
        try:
            result = cleanup_generated_files()
            if isinstance(result, dict):
                if result.get('deleted'):
                    print(f"[cleanup] removed uploads: {result.get('deleted')}")
                if result.get('archive_deleted'):
                    print(f"[cleanup] removed archives: {result.get('archive_deleted')}")
            else:
                if result:
                    print(f"[cleanup] removed: {result}")
        except Exception as e:
            print("[cleanup] error:", e)
        time.sleep(interval)


@app.route('/admin/cleanup', methods=['POST'])
def admin_cleanup():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    # Restrict manual cleanup to the first user (admin) to avoid abuse
    if session.get('user_id') != 1:
        return jsonify({'error': 'Forbidden'}), 403
    deleted = cleanup_generated_files()
    return jsonify({'deleted': deleted})
 
@app.route('/api/scan', methods=['POST'])
def scan():
    """Accept an image and optional lat/lon and return a simple scan result and nearby hospitals."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    image_file = request.files.get('image')
    if not image_file or image_file.filename == '':
        return jsonify({'error': 'Image file is required'}), 400

    filename = secure_filename(image_file.filename)
    unique_filename = f"{secrets.token_hex(8)}_{filename}"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
    image_file.save(file_path)
    image_path = f"static/uploads/{unique_filename}"

    scan_text = "Image received."
    try:
        from PIL import Image
        img = Image.open(os.path.join(app.root_path, image_path))
        w, h = img.size
        scan_text = f"Image received ({w}x{h})."
        # Try AI analysis if key is available
        active_key = get_gemini_api_key()
        if active_key:
            try:
                # Instruct Gemini to analyze the image and return JSON only.
                system_instruction = (
                    "You are Pawsense, an expert veterinary assistant. Analyze the attached pet image "
                    "and provide a structured JSON object with these fields:\n"
                    "- observations: an array of objects {label, confidence (0-1), description}\n"
                    "- severity: one of [low, medium, high, urgent]\n"
                    "- recommended_action: short guidance (one sentence)\n"
                    "- confidence_overall: a number 0-1\n"
                    "- tags: array of short keywords\n"
                    "Output ONLY valid JSON. If uncertain, make conservative suggestions and set lower confidence values."
                )

                prompt_parts = [system_instruction, img]
                for m_name in ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.5-pro"]:
                    try:
                        model = genai.GenerativeModel(m_name)
                        response = model.generate_content(prompt_parts)
                        scan_text = response.text
                        break
                    except Exception as m_err:
                        if "leaked" in str(m_err).lower() or "PERMISSION_DENIED" in str(m_err):
                            raise m_err
                        continue

                # Attempt to parse JSON from the model output
                scan_analysis = None
                try:
                    scan_analysis = json.loads(scan_text)
                except Exception:
                    # Try to extract the first JSON object in the text
                    try:
                        start = scan_text.find('{')
                        end = scan_text.rfind('}')
                        if start != -1 and end != -1 and end > start:
                            scan_analysis = json.loads(scan_text[start:end+1])
                    except Exception:
                        scan_analysis = None
            except Exception:
                scan_analysis = None
    except Exception as e:
        scan_text = f"Error processing image: {e}"

    lat = request.form.get('lat')
    lon = request.form.get('lon')
    hospitals = []
    if lat and lon:
        hospitals = get_nearby_hospitals(lat, lon)

    # Save a JSON report for this scan so clients can request PDF/JSON later
    report = {
        'title': f'PawSense Scan Report - {unique_filename}',
        'image_path': image_path,
        'scan_result': scan_text,
        'scan_analysis': scan_analysis if 'scan_analysis' in locals() else None,
        'nearby_hospitals': hospitals
    }
    try:
        report_filename = f"{unique_filename}.json"
        report_path = os.path.join(app.config['REPORT_FOLDER'], report_filename)
        with open(report_path, 'w', encoding='utf-8') as rf:
            json.dump(report, rf, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return jsonify({
        'scan_result': scan_text,
        'scan_analysis': scan_analysis if 'scan_analysis' in locals() else None,
        'image_path': image_path,
        'nearby_hospitals': hospitals
    })


@app.route('/api/nearby-vets', methods=['GET', 'POST'])
def api_nearby_vets():
    """Return nearby veterinary clinics based on provided latitude and longitude."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    lat = request.args.get('lat') or (request.get_json(silent=True) or {}).get('lat') or request.form.get('lat')
    lon = request.args.get('lon') or (request.get_json(silent=True) or {}).get('lon') or request.form.get('lon')

    # If coordinates are missing, try IP geolocation fallback
    if not lat or not lon:
        try:
            forwarded = request.headers.get('X-Forwarded-For')
            client_ip = forwarded.split(',')[0].strip() if forwarded else request.remote_addr
            endpoint = f"http://ip-api.com/json/{client_ip}" if (client_ip and client_ip not in ('127.0.0.1', '::1', 'localhost')) else "http://ip-api.com/json/"
            ip_resp = requests.get(endpoint, timeout=4)
            if ip_resp.status_code == 200:
                ip_data = ip_resp.json()
                if ip_data.get('status') == 'success' or ip_data.get('lat'):
                    lat = ip_data.get('lat')
                    lon = ip_data.get('lon')
        except Exception:
            pass

    if not lat or not lon:
        return jsonify({'error': 'Latitude and longitude are required. Please enable location services in your browser.'}), 400

    try:
        lat = float(lat)
        lon = float(lon)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid coordinates'}), 400

    hospitals = get_nearby_hospitals(lat, lon)
    return jsonify({
        'nearby_hospitals': hospitals,
        'lat': lat,
        'lon': lon,
        'maps_search_url': f"https://www.google.com/maps/search/veterinary+hospital/@{lat},{lon},14z"
    })


@app.route('/api/scan/report', methods=['GET'])
def get_scan_report():
    """Return a JSON or PDF report for a given image filename.
    Query params: image=<filename> (e.g. 9a8b_image.jpg), format=pdf|json (default json)
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    image = request.args.get('image')
    fmt = (request.args.get('format') or 'json').lower()
    if not image:
        return jsonify({'error': 'image filename required'}), 400

    # Expected report JSON path
    report_name = f"{image}.json" if not image.endswith('.json') else image
    report_path = os.path.join(app.config['REPORT_FOLDER'], report_name)
    if not os.path.exists(report_path):
        return jsonify({'error': 'Report not found'}), 404

    if fmt == 'json':
        return send_file(report_path, mimetype='application/json', as_attachment=True, download_name=report_name)

    # Generate a simple PDF if requested
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
    except Exception:
        return jsonify({'error': 'PDF generation libraries not installed'}), 501

    # Load JSON
    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    # Create PDF into memory
    from io import BytesIO
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    title = report.get('title', 'PawSense Scan Report')
    c.setFont('Helvetica-Bold', 16)
    c.drawString(40, height - 60, title)
    c.setFont('Helvetica', 10)
    y = height - 90
    # Try to include image if exists
    image_file = report.get('image_path')
    if image_file:
        img_path = os.path.join(app.root_path, 'static', 'uploads', image_file.split('/')[-1])
        try:
            img = ImageReader(img_path)
            iw, ih = img.getSize()
            max_w = width - 80
            scale = min(1, max_w / iw)
            draw_w = iw * scale
            draw_h = ih * scale
            c.drawImage(img, 40, y - draw_h, width=draw_w, height=draw_h)
            y -= draw_h + 12
        except Exception:
            pass

    # Write observations
    obs = report.get('scan_analysis', {}).get('observations', [])
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, 'Observations:')
    y -= 18
    c.setFont('Helvetica', 10)
    for o in obs:
        text = f"- {o.get('label')}: {o.get('description','')} ({int((o.get('confidence') or 0)*100)}%)"
        for line in textwrap.wrap(text, 100):
            if y < 80:
                c.showPage(); y = height - 60; c.setFont('Helvetica', 10)
            c.drawString(44, y, line)
            y -= 14

    # Recommended action
    ra = report.get('scan_analysis', {}).get('recommended_action')
    if ra:
        if y < 120:
            c.showPage(); y = height - 60
        c.setFont('Helvetica-Bold', 12); c.drawString(40, y, 'Recommended Action:'); y -= 18
        c.setFont('Helvetica', 10)
        for line in textwrap.wrap(ra, 100):
            c.drawString(44, y, line); y -= 14

    c.showPage(); c.save(); buf.seek(0)
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'pawsense-report-{image}.pdf')


if __name__ == '__main__':
    try:
        t = threading.Thread(target=_cleanup_worker, daemon=True)
        t.start()
    except Exception:
        pass
    # Disable the reloader to avoid watchdog-related thread restart issues
    app.run(debug=True, port=5000, use_reloader=False)
