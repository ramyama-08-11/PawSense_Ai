"""Ensure the database tables exist by importing the Flask app and calling create_all().
Run with: python scripts/ensure_db.py
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app
from extensions import db

def run():
    with app.app_context():
        db.create_all()
        print('Database tables ensured (create_all executed)')

if __name__ == '__main__':
    run()
