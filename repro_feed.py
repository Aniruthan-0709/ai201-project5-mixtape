from datetime import datetime, timezone
from app import create_app, db
from models import User, Song, ListeningEvent
from services.feed_service import get_friends_listening_now

app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})

with app.app_context():
    db.create_all()

    user_a = User(username="alice", email="alice@example.com")
    user_b = User(username="bob", email="bob@example.com")
    db.session.add_all([user_a, user_b])
    db.session.flush()
    user_a.friends.append(user_b)

    song = Song(title="Test Song", artist="Test Artist", shared_by=user_a.id)
    db.session.add(song)
    db.session.flush()

    # Explicitly: Bob listened late on July 4th (yesterday), should be EXCLUDED
    yesterday_late = datetime(2026, 7, 4, 23, 30, tzinfo=timezone.utc)
    event_yesterday = ListeningEvent(user_id=user_b.id, song_id=song.id, listened_at=yesterday_late)
    db.session.add(event_yesterday)
    db.session.commit()

    results = get_friends_listening_now(user_a.id)
    print("Case 1 — Bob listened 11:30pm YESTERDAY. Shown as listening now?", len(results) > 0)

    # Now move Bob's event to early TODAY, should be INCLUDED
    event_yesterday.listened_at = datetime(2026, 7, 5, 0, 30, tzinfo=timezone.utc)
    db.session.commit()

    results = get_friends_listening_now(user_a.id)
    print("Case 2 — Bob listened 12:30am TODAY. Shown as listening now?", len(results) > 0)