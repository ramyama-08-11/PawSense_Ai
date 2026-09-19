#!/usr/bin/env python3
"""Helper to set Google API keys into a .env file safely.
Usage:
  python scripts/set_keys.py --server <SERVER_KEY> --browser <BROWSER_KEY> --genai <GENAI_KEY>
Or run without flags to be prompted (input hidden).
This script backs up an existing `.env` to `.env.bak` before modifying.
"""
import os
import argparse
import getpass
import shutil

ENV_PATH = '.env'
BACKUP_PATH = '.env.bak'


def load_env(path):
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k, v = s.split('=', 1)
            data[k] = v
    return data


def write_env(path, updates):
    # If the file exists, preserve comments and unknown keys, update known keys
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            orig_lines = f.read().splitlines()
        out_lines = []
        updated_keys = set()
        for line in orig_lines:
            if line.strip().startswith('#') or '=' not in line:
                out_lines.append(line)
                continue
            k = line.split('=', 1)[0]
            if k in updates:
                out_lines.append(f"{k}={updates[k]}")
                updated_keys.add(k)
            else:
                out_lines.append(line)
        # Append any keys not present
        for k, v in updates.items():
            if k not in updated_keys:
                out_lines.append(f"{k}={v}")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out_lines) + '\n')
    else:
        # Create new file
        with open(path, 'w', encoding='utf-8') as f:
            for k, v in updates.items():
                f.write(f"{k}={v}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', help='GOOGLE_SERVER_KEY')
    parser.add_argument('--browser', help='GOOGLE_BROWSER_KEY')
    parser.add_argument('--genai', help='GOOGLE_API_KEY / Gemini key')
    args = parser.parse_args()

    server = args.server
    browser = args.browser
    genai = args.genai

    try:
        if not server:
            server = getpass.getpass('Enter GOOGLE_SERVER_KEY (leave blank to skip): ').strip() or None
        if not browser:
            browser = getpass.getpass('Enter GOOGLE_BROWSER_KEY (leave blank to skip): ').strip() or None
        if not genai:
            genai = getpass.getpass('Enter GOOGLE_API_KEY (leave blank to skip): ').strip() or None
    except (KeyboardInterrupt, EOFError):
        print('\nInput cancelled.')
        return

    updates = {}
    if server:
        updates['GOOGLE_SERVER_KEY'] = server
    if browser:
        updates['GOOGLE_BROWSER_KEY'] = browser
    if genai:
        updates['GOOGLE_API_KEY'] = genai

    if not updates:
        print('No keys provided. Nothing to do.')
        return

    # Backup existing .env
    if os.path.exists(ENV_PATH):
        print(f'Backing up existing {ENV_PATH} to {BACKUP_PATH}')
        shutil.copy2(ENV_PATH, BACKUP_PATH)

    existing = load_env(ENV_PATH)
    existing.update(updates)
    write_env(ENV_PATH, existing)

    print(f'Updated {ENV_PATH} with provided keys. Backup saved to {BACKUP_PATH} if it existed.')


if __name__ == '__main__':
    main()
