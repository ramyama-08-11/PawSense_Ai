import os
import json
import pytest
from PIL import Image

import image_generator as ig
import app as app_mod
from app import app, db
from models import User, ChatSession, Message


def test_detect_image_intent():
    # Slash commands
    is_img, p = ig.detect_image_intent('/image cute golden retriever puppy')
    assert is_img is True
    assert p == 'cute golden retriever puppy'

    is_img, p = ig.detect_image_intent('/imagine fluffy white cat')
    assert is_img is True
    assert p == 'fluffy white cat'

    # Natural language queries
    is_img, p = ig.detect_image_intent('Can you generate an image of a playful Beagle?')
    assert is_img is True
    assert 'Beagle' in p

    is_img, p = ig.detect_image_intent('Please create a picture of healthy dog food')
    assert is_img is True
    assert 'dog food' in p

    is_img, p = ig.detect_image_intent('Draw a cute parrot flying in the sky')
    assert is_img is True
    assert 'parrot' in p

    # Negative non-image queries
    is_img, p = ig.detect_image_intent('My dog is coughing at night, what should I do?')
    assert is_img is False

    is_img, p = ig.detect_image_intent('Find nearest veterinary clinics')
    assert is_img is False


def test_enhance_prompt_styles():
    p = "Persian kitten"
    enhanced_photo = ig.enhance_prompt(p, style='photorealistic')
    assert 'studio pet photography' in enhanced_photo

    enhanced_3d = ig.enhance_prompt(p, style='cute-3d')
    assert 'Pixar Disney style' in enhanced_3d

    enhanced_clinic = ig.enhance_prompt(p, style='veterinary')
    assert 'veterinary' in enhanced_clinic


def test_generate_pet_image_procedural(tmp_path):
    upload_dir = str(tmp_path / 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    result = ig.generate_pet_image(
        prompt="Adorable Golden Retriever puppy",
        style="cute-3d",
        upload_folder=upload_dir
    )

    assert result['success'] is True
    assert result['filename'].endswith('.png')
    assert result['thumb_filename'].endswith('.webp') or result['thumb_filename'].endswith('.png')
    assert os.path.exists(os.path.join(upload_dir, result['filename']))

    # Verify PIL can open the generated image
    img = Image.open(os.path.join(upload_dir, result['filename']))
    assert img.size[0] > 0 and img.size[1] > 0


def test_api_chat_image_generation(tmp_path, monkeypatch):
    # Mock AI reply so tests run fast without waiting for network timeouts
    monkeypatch.setattr(app_mod, 'generate_ai_reply', lambda *args, **kwargs: "Here is your generated golden retriever puppy!")

    client = app.test_client()

    with app.app_context():
        user = User.query.filter_by(username='img_test_user').first()
        if not user:
            user = User(username='img_test_user', password_hash='hash')
            db.session.add(user)
            db.session.commit()

        user_id = user.id
        session_obj = ChatSession(user_id=user_id, title="Test Chat")
        db.session.add(session_obj)
        db.session.commit()
        session_id = session_obj.id

    with client.session_transaction() as sess:
        sess['user_id'] = user_id

    resp = client.post('/api/chat', data={
        'session_id': session_id,
        'content': 'Can you generate an image of a cute puppy playing in the grass?',
        'style': 'photorealistic'
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert 'model_message' in data
    assert data['model_message']['image_path'] is not None
    assert 'protected_uploads/generated_' in data['model_message']['image_path']
    assert data['model_message'].get('is_generated_image') is True


def test_api_generate_image_endpoint(monkeypatch):
    monkeypatch.setattr(app_mod, 'generate_ai_reply', lambda *args, **kwargs: "Here is your Siberian husky in snow!")

    client = app.test_client()

    with app.app_context():
        user = User.query.filter_by(username='img_test_user').first()
        if not user:
            user = User(username='img_test_user', password_hash='hash')
            db.session.add(user)
            db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess['user_id'] = user_id

    resp = client.post('/api/generate-image', json={
        'prompt': 'Siberian husky in snow',
        'style': 'photorealistic',
        'language': 'English'
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert 'session_id' in data
    assert 'model_message' in data
    assert data['model_message']['image_path'] is not None
    assert 'protected_uploads/generated_' in data['model_message']['image_path']
