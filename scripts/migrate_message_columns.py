#!/usr/bin/env python3
"""
One-shot migration helper: adds `archived` and `archive_path` columns
to the `message` table in the local SQLite DB if they're missing.

Usage:
  python scripts/migrate_message_columns.py

This is a simple helper for local dev. For production use Alembic migrations.
"""
import os
from sqlalchemy import create_engine, text


def main():
    db_url = os.environ.get('DATABASE_URL') or 'sqlite:///pawsense.db'
    print('Connecting to', db_url)
    engine = create_engine(db_url)
    with engine.connect() as conn:
        try:
            res = conn.execute(text("PRAGMA table_info('message')"))
            cols = [row[1] for row in res]
        except Exception as e:
            print('Failed to read table info:', e)
            return

        if 'archived' in cols and 'archive_path' in cols:
            print('Columns already present: archived, archive_path')
            return

        if 'archived' not in cols:
            try:
                print('Adding column: archived')
                conn.execute(text("ALTER TABLE message ADD COLUMN archived INTEGER DEFAULT 0"))
            except Exception as e:
                print('Failed to add archived:', e)

        if 'archive_path' not in cols:
            try:
                print('Adding column: archive_path')
                conn.execute(text("ALTER TABLE message ADD COLUMN archive_path VARCHAR(255)"))
            except Exception as e:
                print('Failed to add archive_path:', e)

        print('Migration complete.')


if __name__ == '__main__':
    main()
