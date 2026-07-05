from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications

app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})

with app.app_context():
    db.create_all()

    sharer = User(username="sharer", email="sharer@example.com")
    rater = User(username="rater", email="rater@example.com")
    db.session.add_all([sharer, rater])
    db.session.flush()

    song = Song(title="Test Song", artist="Test Artist", shared_by=sharer.id)
    db.session.add(song)
    db.session.commit()

    # Rater rates the sharer's song
    rate_song(rater.id, song.id, 5)

    # Check: did the sharer get notified?
    notifications = get_notifications(sharer.id)
    print("Sharer's notifications:", notifications)
    print("Count:", len(notifications))