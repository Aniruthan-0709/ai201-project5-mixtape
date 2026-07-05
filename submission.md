# Mixtape — Codebase Map

## Main Files

- `app.py`: Flask application factory. Creates a single shared `db` (SQLAlchemy) instance, configures the database URI (SQLite by default), registers all four route blueprints with their URL prefixes (`/songs`, `/playlists`, `/users`, `/feed`), and calls `db.create_all()` to create tables on startup.
- `models.py`: Defines all SQLAlchemy models — `User`, `Song`, `Tag`, `ListeningEvent`, `Rating`, `Playlist`, `Notification` — plus three association tables: `friendships` (symmetric many-to-many between users), `song_tags` (many-to-many for tags), and `playlist_entries` (many-to-many between playlists and songs, but with extra columns: `position`, `added_by`, `added_at` — songs in a playlist have an explicit order, not just insertion order).
- `routes/songs.py`: Endpoints for searching songs, viewing a song, rating a song, and logging a listen. Each handler parses the request and delegates to a service function.
- `routes/playlists.py`: Endpoints for creating a playlist, viewing playlist metadata, listing songs in a playlist, and adding a song to a playlist.
- `routes/users.py`: Endpoints for viewing a user profile, checking a user's streak, listing notifications, and marking a notification read.
- `routes/feed.py`: Endpoints for "friends listening now" and the general activity feed.
- `services/streak_service.py`: Owns listening-streak logic. `record_listening_event()` logs a `ListeningEvent` and updates the user's streak in the same transaction. `update_listening_streak()` contains the day-comparison rules that decide whether the streak increments, resets, or stays the same.
- `services/feed_service.py`: Builds the two friend-facing feeds. `get_friends_listening_now()` filters `ListeningEvent` rows to the last 24 hours and de-duplicates to one (most recent) entry per friend. `get_activity_feed()` skips the time filter entirely and just returns the most recent N events.
- `services/search_service.py`: Case-insensitive search over song title/artist, plus a single-song lookup.
- `services/notification_service.py`: The largest service — owns notification creation/reads AND two write operations that have a notification as a side effect: `rate_song()` (upserts a `Rating`) and `add_to_playlist()` (appends a song to a playlist and notifies the original sharer).
- `services/playlist_service.py`: Playlist creation and retrieval — `get_playlist_songs()` joins `Song` to `playlist_entries` ordered by `position`.
- `seed_data.py`: Populates the database with sample users, songs, playlists, etc. so the app has data to interact with locally; run once before `flask run`.

## Data Flow: Rating a Song

1. Client sends `POST /songs/<song_id>/rate` with `{user_id, score}` in the body.
2. `routes/songs.py`'s `rate()` handler validates that `user_id` and `score` are present, then calls `notification_service.rate_song(user_id, song_id, score)`.
3. Inside `rate_song()`: validates `score` is 1–5, looks up the `Song` and `User` (raises `ValueError` → 404 if either is missing), then checks for an existing `Rating` with that `(user_id, song_id)` pair (there's a `UniqueConstraint` on that pair in `models.py`, so this table can only ever have one rating per user per song). If one exists, it updates the score; otherwise it creates a new `Rating` row.
4. Commits and returns the `Rating`, which the route serializes via `.to_dict()` back to the client as JSON.
5. Notably, `rate_song()` never calls `create_notification()` — unlike the sibling function `add_to_playlist()` in the same file, which does notify the song's original sharer after adding a song to a playlist. This asymmetry is the likely root cause behind Issue #4 ("notified when added to playlist but not when rated").

## Patterns I Noticed

- **Routes are thin, services hold logic.** Every route handler's job is limited to: parse the request, call one service function, format the response and map exceptions (`ValueError` → 400/404). No route file contains business logic itself.
- **Services are organized by side effect, not by data type.** There's no standalone `rating_service.py` even though `Rating` is its own model — rating logic lives in `notification_service.py` because rating a song is conceptually "an action that might need to notify someone." The same is true for `add_to_playlist()`, which lives there instead of in `playlist_service.py`.
- **Shared `db` instance pattern.** Every model and service imports the same `db` object from `app.py` rather than each module creating its own — this is what lets a single `record_listening_event()` call commit both a `ListeningEvent` insert and a `User` streak update atomically.