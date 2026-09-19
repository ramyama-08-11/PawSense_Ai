PAWSENSE — Local Setup

Quick start

1. Copy the example env and fill in your keys:

```bash
cp .env.example .env
# then edit .env and add your keys
```

2. Recommended Google keys and API enablement:
- Create/select a Google Cloud project and enable billing.
- Enable these APIs:
  - Maps JavaScript API (for embedded maps in the browser)
  - Places API / Places SDK for Web (for nearby search from the server)
- Create two API keys:
  - `GOOGLE_BROWSER_KEY` — Browser key restricted by HTTP referrers (e.g. `http://localhost:5000/*`). Used by the Maps JS client.
  - `GOOGLE_SERVER_KEY` — Server key restricted by IP (your server). Used by the Places REST API.
- (Optional) `GOOGLE_API_KEY` is used by the server for Gemini / Generative AI features.

3. Install dependencies and run:

```bash
pip install -r requirements.txt
python app.py
```

Notes
- For best security: restrict `GOOGLE_BROWSER_KEY` to allowed referrers and `GOOGLE_SERVER_KEY` to server IPs.
- If you do not provide keys the app will still run but maps and Places features will fall back to Google Maps links or limited behavior.

If you want I can add a `scripts/set_keys.py` helper to write keys into `.env` securely.
Optional helper script
----------------------

A convenience script is available to write keys into your `.env` file and backup any existing file:

```bash
python scripts/set_keys.py --server YOUR_SERVER_KEY --browser YOUR_BROWSER_KEY --genai YOUR_GENAI_KEY
```

Run without flags to be prompted (input is hidden). The script backs up an existing `.env` to `.env.bak` before updating.
