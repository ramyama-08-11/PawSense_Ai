"""Simple migration helper: adds image_caption column if missing.
Run with: python scripts/add_image_caption_migration.py
"""
import os
import sys
# Ensure project root is on sys.path so imports work when run from scripts/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import sqlite3

def run():
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'pawsense.db'))
    if not os.path.isfile(db_path):
        print('Database file not found at', db_path)
        return
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info('message')")
        cols = [row[1] for row in cur.fetchall()]
        if 'image_caption' not in cols:
            try:
                cur.execute("ALTER TABLE message ADD COLUMN image_caption VARCHAR(255)")
                conn.commit()
                print('Added image_caption column to message table')
            except Exception as e:
                print('Failed to add column:', e)
        else:
            print('image_caption column already present')
        cur.close()
        conn.close()
    except Exception as e:
        print('Migration failed:', e)

if __name__ == '__main__':
    run()
