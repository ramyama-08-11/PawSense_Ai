import io
import os
import json
import importlib
import importlib.util
import sys
import tempfile

import pytest
from sqlalchemy import func


# Load the app module by path to make tests robust to import paths
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
APP_PATH = os.path.join(ROOT, 'app.py')
spec = importlib.util.spec_from_file_location('pawsense_app', APP_PATH)
app_mod = importlib.util.module_from_spec(spec)
sys.modules['pawsense_app'] = app_mod
sys.path.insert(0, ROOT)
spec.loader.exec_module(app_mod)
sys.path.pop(0)


@pytest.fixture(autouse=True)
def app_and_db(tmp_path):
    # ensure uploads and reports are isolated
    upload_dir = app_mod.app.config['UPLOAD_FOLDER']
    report_dir = app_mod.app.config['REPORT_FOLDER']
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)
    # Create a fresh user for tests
    from extensions import db
    with app_mod.app.app_context():
        db.create_all()
        # create test user if not exists
        from models import User
        u = User.query.filter_by(username='testuser').first()
        if not u:
            u = User(username='testuser', password_hash='x')
            db.session.add(u)
            db.session.commit()
        yield
        # teardown: remove uploaded files and reports
        for f in os.listdir(upload_dir):
            try: os.remove(os.path.join(upload_dir, f))
            except Exception: pass
        for f in os.listdir(report_dir):
            try: os.remove(os.path.join(report_dir, f))
            except Exception: pass


def test_get_nearby_hospitals_monkeypatch(monkeypatch):
    # Fake requests.get to return a sample Places response
    class DummyResp:
        def json(self):
            return {
                'results': [
                    {'name': 'Vet One', 'vicinity': '123 Paw St', 'geometry': {'location': {'lat': 1.1, 'lng': 2.2}}, 'rating': 4.5}
                ]
            }

    def fake_get(url, params=None, timeout=None):
        return DummyResp()

    monkeypatch.setattr(app_mod, 'requests', type('R', (), {'get': fake_get}))
    res = app_mod.get_nearby_hospitals(1.1, 2.2)
    assert isinstance(res, list)
    assert res and res[0]['name'] == 'Vet One'


def make_test_image():
    from PIL import Image
    bio = io.BytesIO()
    img = Image.new('RGB', (16, 16), color=(155, 0, 0))
    img.save(bio, format='JPEG')
    bio.seek(0)
    return bio


def test_scan_endpoint_upload(client=None):
    # Use Flask test client
    c = app_mod.app.test_client()
    # get user id
    from models import User
    from extensions import db
    with app_mod.app.app_context():
        u = User.query.filter_by(username='testuser').first()
        assert u is not None

    # set session user_id
    with c.session_transaction() as sess:
        sess['user_id'] = u.id

    img = make_test_image()
    data = {'image': (img, 'pet.jpg')}
    resp = c.post('/api/scan', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    jd = resp.get_json()
    assert 'scan_result' in jd
    assert 'image_path' in jd
    # report file should have been created
    report_file = os.path.join(app_mod.app.config['REPORT_FOLDER'], os.path.basename(jd['image_path']) + '.json')
    assert os.path.exists(report_file)


def test_chat_returns_fallback_when_model_has_no_text(monkeypatch):
    c = app_mod.app.test_client()
    from models import User, ChatSession
    from extensions import db

    with app_mod.app.app_context():
        u = User.query.filter_by(username='testuser').first()
        user_id = u.id
        s = ChatSession(user_id=user_id, title='Test chat')
        db.session.add(s)
        db.session.commit()
        session_id = s.id

    class FakeResponse:
        text = ''
        candidates = []
        prompt_feedback = type('Feedback', (), {'block_reason': None})()

    class FakeModel:
        def generate_content(self, parts):
            return FakeResponse()

    monkeypatch.setattr(app_mod, 'api_key', 'dummy-key')
    monkeypatch.setattr(app_mod.genai, 'GenerativeModel', lambda *_args, **_kwargs: FakeModel())

    with c.session_transaction() as sess:
        sess['user_id'] = user_id

    resp = c.post('/api/chat', data={'session_id': str(session_id), 'content': 'hello', 'language': 'English'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'meaningful reply' in data['model_message']['content']


def test_transcribe_audio_endpoint(monkeypatch):
    c = app_mod.app.test_client()
    from models import User

    with app_mod.app.app_context():
        u = User.query.filter_by(username='testuser').first()

    with c.session_transaction() as sess:
        sess['user_id'] = u.id

    class DummyResp:
        status_code = 200

        def json(self):
            return {'results': [{'alternatives': [{'transcript': 'hello there from audio'}]}]}

    def fake_post(url, json=None, timeout=None, params=None):
        return DummyResp()

    monkeypatch.setattr(app_mod, 'requests', type('R', (), {'post': staticmethod(fake_post)}))

    audio = io.BytesIO(b'fake-audio-data')
    resp = c.post('/api/transcribe', data={'audio': (audio, 'clip.webm')}, content_type='multipart/form-data')

    assert resp.status_code == 200
    assert resp.get_json()['transcript'] == 'hello there from audio'


def test_mock_social_login_reuses_existing_user_case_insensitive():
    c = app_mod.app.test_client()
    from extensions import db
    from models import User
    from werkzeug.security import generate_password_hash

    with app_mod.app.app_context():
        User.query.filter_by(username='RAMYAMA457@GMAIL.COM').delete()
        User.query.filter_by(username='ramyama457@gmail.com').delete()
        db.session.commit()

        existing = User(username='RAMYAMA457@GMAIL.COM', password_hash=generate_password_hash('secretpass'))
        db.session.add(existing)
        db.session.commit()

        assert User.query.filter_by(username='RAMYAMA457@GMAIL.COM').count() == 1

        resp = c.post('/mock_social_login/google', data={
            'username': 'ramyama457@gmail.com',
            'password': 'secretpass'
        }, follow_redirects=False)

        assert resp.status_code == 302
        assert User.query.filter(func.lower(User.username) == 'ramyama457@gmail.com').count() == 1


def test_google_oauth_login_redirects_to_google_when_configured(monkeypatch):
    c = app_mod.app.test_client()
    monkeypatch.setattr(app_mod, 'GOOGLE_CLIENT_ID', 'client-id-123')
    monkeypatch.setattr(app_mod, 'GOOGLE_CLIENT_SECRET', 'client-secret-456')
    monkeypatch.setattr(app_mod, 'GOOGLE_REDIRECT_URI', 'http://127.0.0.1:5000/auth/google/callback')

    resp = c.get('/auth/google/login', follow_redirects=False)

    assert resp.status_code == 302
    assert 'accounts.google.com' in resp.headers['Location']
    assert 'client_id=client-id-123' in resp.headers['Location']


def test_haversine_distance():
    # Distance between Bangalore (12.9716, 77.5946) and nearby point
    d = app_mod.haversine_distance(12.9716, 77.5946, 12.9720, 77.5950)
    assert d is not None
    assert d >= 0


def test_get_nearby_hospitals_osm_fallback(monkeypatch):
    class DummyPlacesResp:
        def json(self):
            return {'results': [], 'status': 'ZERO_RESULTS'}

    class DummyOSMResp:
        status_code = 200
        def json(self):
            return {
                'elements': [
                    {
                        'lat': 12.92, 'lon': 77.58,
                        'tags': {'name': 'OSM Pet Care Clinic', 'addr:street': 'MG Road'}
                    }
                ]
            }

    def fake_get(url, params=None, timeout=None):
        return DummyPlacesResp()

    def fake_post(url, data=None, headers=None, timeout=None):
        return DummyOSMResp()

    monkeypatch.setattr(app_mod, 'requests', type('R', (), {'get': fake_get, 'post': fake_post}))
    res = app_mod.get_nearby_hospitals(12.9716, 77.5946)
    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]['name'] == 'OSM Pet Care Clinic'
    assert res[0]['distance_km'] is not None


def test_api_nearby_vets_requires_login():
    c = app_mod.app.test_client()
    resp = c.get('/api/nearby-vets?lat=12.97&lon=77.59')
    assert resp.status_code == 401
