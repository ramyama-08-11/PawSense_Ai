from datetime import datetime
from extensions import db

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sessions = db.relationship('ChatSession', backref='user', lazy=True, cascade="all, delete-orphan")

class ChatSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False, default="New Chat")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages = db.relationship('Message', backref='session', lazy=True, cascade="all, delete-orphan")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('chat_session.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'user' or 'model'
    content = db.Column(db.Text, nullable=False)
    image_path = db.Column(db.String(255), nullable=True) # Relative path to attached image
    image_caption = db.Column(db.String(255), nullable=True)
    # Support multiple images per message (JSON arrays stored as text)
    image_paths = db.Column(db.Text, nullable=True)
    image_captions = db.Column(db.Text, nullable=True)
    structured = db.Column(db.Text, nullable=True) # JSON string for structured AI outputs
    archived = db.Column(db.Boolean, default=False)
    archive_path = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
