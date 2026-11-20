import os
import io
import pytest
from app import app, db
from models import User, ChatSession, Message
import pdf_generator as pg


def test_pdf_clean_markdown():
    raw = "Here is **bold text** and *italic text*:\n- Item 1\n- Item 2"
    cleaned = pg.clean_markdown_for_pdf(raw)
    assert '<b>bold text</b>' in cleaned
    assert '<i>italic text</i>' in cleaned
    assert '•' in cleaned


def test_generate_single_message_pdf_text_only(tmp_path):
    with app.app_context():
        user = User.query.filter_by(username='pdf_test_user').first()
        if not user:
            user = User(username='pdf_test_user', password_hash='hash')
            db.session.add(user)
            db.session.commit()

        session_obj = ChatSession(user_id=user.id, title="PDF Test")
        db.session.add(session_obj)
        db.session.commit()

        msg = Message(session_id=session_obj.id, role='model', content="Your dog needs plenty of water and rest.")
        db.session.add(msg)
        db.session.commit()

        pdf_bio = pg.generate_single_message_pdf(msg, user, str(tmp_path))
        pdf_bytes = pdf_bio.getvalue()
        assert pdf_bytes.startswith(b'%PDF')
        assert len(pdf_bytes) > 500


def test_generate_session_pdf_endpoint(tmp_path):
    client = app.test_client()

    with app.app_context():
        user = User.query.filter_by(username='pdf_test_user').first()
        if not user:
            user = User(username='pdf_test_user', password_hash='hash')
            db.session.add(user)
            db.session.commit()

        session_obj = ChatSession(user_id=user.id, title="Complete Consultation")
        db.session.add(session_obj)
        db.session.commit()

        u_msg = Message(session_id=session_obj.id, role='user', content="My cat is coughing.")
        m_msg = Message(session_id=session_obj.id, role='model', content="Keep the cat warm and watch for wheezing.")
        db.session.add_all([u_msg, m_msg])
        db.session.commit()
        session_id = session_obj.id
        msg_id = m_msg.id
        user_id = user.id

    with client.session_transaction() as sess:
        sess['user_id'] = user_id

    # Test Session PDF export
    resp_session = client.get(f'/api/sessions/{session_id}/export_pdf')
    assert resp_session.status_code == 200
    assert resp_session.mimetype == 'application/pdf'
    assert resp_session.data.startswith(b'%PDF')

    # Test Single Message PDF export
    resp_msg = client.get(f'/api/message/report_pdf/{msg_id}')
    assert resp_msg.status_code == 200
    assert resp_msg.mimetype == 'application/pdf'
    assert resp_msg.data.startswith(b'%PDF')
