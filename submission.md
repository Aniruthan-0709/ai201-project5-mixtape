# Mixtape — Codebase Map

## Main Files

- `app.py`: Flask application factory. Creates a single shared `db` (SQLAlchemy) instance, configures the database URI (SQLite by default), registers all four route blueprints with their URL prefixes (`/songs`, `/playlists`, `/users`, `/feed`), and calls `db.create_all()` to create tables on startup.
- `models.py`: Defines all SQLAlchemy models — `User`, `Song`, `Tag`, `ListeningEvent`, `Rating`, `Playlist`, `Notification` — plus three association tables: `friendships` (symmetric many-to-many between users), `song_tags` (many-to-many for tags), and `playlist_entries` (many-to-many between playlists and songs, but with extra columns: `position`, `added_by`, `added_at` — songs in a playlist have an explicit order, not just insertion order).
- `routes/songs.py`: Endpoints for searching songs, viewing a song, rating a song, and logging a listen. Each handler parses the request and delegates to a service function.
- `routes/playlists.py`: Endpoints for creating a playlist, viewing playlist metadata, listing songs in a playlist, and adding a song to a playlist.
- `routes/users.py`: Endpoints for viewing a user profile, checking a user's streak, listing notifications, and marking a notification read.
- `routes/feed.py`: Endpoints for "friends listening now" and the general activity feed.
- `services/streak_service.py`: Owns listening-streak logic. `record_listening_event()` logs a `ListeningEvent` and updates the user's streak in the same transaction. `update_listening_streak()` contains the day-comparison rules that decide whether the streak increments, resets, or stays the same.
- `services/feed_service.py`: Builds the two friend-facing feeds. `get_friends_listening_now()` filters `ListeningEvent` rows to a recency window and de-duplicates to one (most recent) entry per friend. `get_activity_feed()` skips the time filter entirely and just returns the most recent N events.
- `services/search_service.py`: Case-insensitive search over song title/artist, plus a single-song lookup.
- `services/notification_service.py`: The largest service — owns notification creation/reads AND two write operations that have a notification as a side effect: `rate_song()` (upserts a `Rating`) and `add_to_playlist()` (appends a song to a playlist and notifies the original sharer).
- `services/playlist_service.py`: Playlist creation and retrieval — `get_playlist_songs()` joins `Song` to `playlist_entries` ordered by `position`.
- `seed_data.py`: Populates the database with sample users, songs, playlists, etc. so the app has data to interact with locally; run once before `flask run`.

## Data Flow: Rating a Song

1. Client sends `POST /songs/<song_id>/rate` with `{user_id, score}` in the body.
2. `routes/songs.py`'s `rate()` handler validates that `user_id` and `score` are present, then calls `notification_service.rate_song(user_id, song_id, score)`.
3. Inside `rate_song()`: validates `score` is 1–5, looks up the `Song` and `User` (raises `ValueError` → 404 if either is missing), then checks for an existing `Rating` with that `(user_id, song_id)` pair (there's a `UniqueConstraint` on that pair in `models.py`, so this table can only ever have one rating per user per song). If one exists, it updates the score; otherwise it creates a new `Rating` row.
4. Commits and returns the `Rating`, which the route serializes via `.to_dict()` back to the client as JSON.
5. Notably, `rate_song()` never calls `create_notification()` — unlike the sibling function `add_to_playlist()` in the same file, which does notify the song's original sharer after adding a song to a playlist. This asymmetry is the root cause behind Issue #4 ("notified when added to playlist but not when rated").

## Patterns I Noticed

- **Routes are thin, services hold logic.** Every route handler's job is limited to: parse the request, call one service function, format the response and map exceptions (`ValueError` → 400/404). No route file contains business logic itself.
- **Services are organized by side effect, not by data type.** There's no standalone `rating_service.py` even though `Rating` is its own model — rating logic lives in `notification_service.py` because rating a song is conceptually "an action that might need to notify someone." The same is true for `add_to_playlist()`, which lives there instead of in `playlist_service.py`.
- **Shared `db` instance pattern.** Every model and service imports the same `db` object from `app.py` rather than each module creating its own — this is what lets a single `record_listening_event()` call commit both a `ListeningEvent` insert and a `User` streak update atomically.

---

## Root Cause Analysis & Fixes

### Issue #1 — My listening streak keeps resetting

**How I reproduced it:** Ran `pytest tests/test_streaks.py -v` before making any changes.
`test_streak_increments_on_sunday` failed: a user who listens on Saturday (streak=1)
and again the next day, Sunday, ended up with `listening_streak == 1` instead of the
expected `2` (`assert 1 == 2`). All 4 other streak tests passed, isolating the failure
to the Sunday-specific case.

**How I found the root cause:** Started in `streak_service.py`, in
`update_listening_streak()`, since that's the function the failing test calls directly.
Compared the function's docstring (which only describes four cases: no prior listen,
same-day, consecutive-day, skipped-day) against its actual code, and noticed the
`elif` condition included `and today.weekday() != 6` — a clause never mentioned in the
docstring. That mismatch between documented behavior and actual code was the signal
that this was the bug, not just a suspicious area.

**The root cause:** Python's `date.weekday()` returns `6` for Sunday. The increment
branch was written as `elif days_since_last == 1 and today.weekday() != 6`, combining
a valid same-day-gap check with an unrelated weekday check using `and`. On Sundays,
`today.weekday() != 6` evaluates to `False`, so the whole `elif` condition is `False`
even when `days_since_last == 1` is `True` — meaning the user genuinely listened on
consecutive days. Execution falls through to the `else` branch, which was intended to
handle skipped days, and the streak gets reset to 1 instead of incremented, purely
because the day happened to be a Sunday.

**My fix and side-effect check:** Removed the `and today.weekday() != 6` clause,
changing the condition to `elif days_since_last == 1:`. This makes the code match
exactly what the docstring documents — no weekday exception. Verified by rerunning
`pytest tests/test_streaks.py -v`: all 5 tests pass, including the previously-failing
Sunday test. Checked the other 4 tests (same-day, consecutive-day, skipped-day, new
user) to confirm none relied on the removed clause — none of them use Sunday dates
except the one testing this exact bug, so no other streak behavior is affected.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it:** Wrote a script with two friended users. Set friend B's
`listened_at` to a fixed timestamp late the previous calendar day (11:30 PM, July
4th). Calling `get_friends_listening_now()` for friend A showed B as "listening now"
(`True`), even though the event happened on a different calendar date.

**How I found the root cause:** Started in `feed_service.py`'s
`get_friends_listening_now()`, since that's the function the route calls directly.
Read the `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` line and confirmed
via a synthetic test that a 30-hour-old event was correctly excluded — ruling out a
simple "filter doesn't work" theory. Realized the filter uses a rolling 24-hour window,
not a calendar-day boundary, and confirmed this by testing an event 23 hours old that
landed on the previous calendar date but still passed the filter.

**The root cause:** `RECENT_THRESHOLD = timedelta(hours=24)` defined "recent" as
"within the last 24 hours," not "since midnight today." Early in the day, subtracting
24 hours from `now` produces a cutoff that falls on the *previous* calendar day, so any
event from late "yesterday" still satisfied `listened_at >= cutoff`. The bug wasn't
that the filter was broken — it correctly enforced a 24-hour window — it's that a
rolling window doesn't match the feature's intended meaning of "today."

**My fix and side-effect check:** Changed the cutoff calculation from
`now - RECENT_THRESHOLD` to `now.replace(hour=0, minute=0, second=0, microsecond=0)`,
making "recent" mean "since midnight UTC today." Removed the now-unused
`RECENT_THRESHOLD` constant and the unused `timedelta` import. Verified on both sides
of the boundary: an event at 11:30 PM the previous day is now correctly excluded
(`False`), and an event at 12:30 AM today is correctly included (`True`). Ran the full
test suite (`pytest tests/ -v`) — `test_streaks.py` (5/5) and `test_search.py` (5/5)
still pass, confirming this change didn't affect unrelated features. Two
`test_playlists.py` failures appeared, but these are a separate, pre-existing bug
(Issue #5 — `get_playlist_songs` drops the last song via a `[:-1]` slice), unrelated
to this fix.

---

## Bug Reproductions (not yet fixed)

### Issue #4 — Notified when added to playlist but not when rated

**How I reproduced it:** Wrote a script where a "rater" user calls `rate_song()` on a
song shared by a "sharer" user. Checked `get_notifications(sharer.id)` afterward — the
result was an empty list (`Count: 0`).

**Root cause candidate:** `rate_song()` looks up the `Song` and `User`, validates the
score, upserts the `Rating`, commits, and returns — it never references
`song.shared_by` and never calls `create_notification()`. This is inconsistent with the
sibling function `add_to_playlist()` in the same file, which explicitly reads
`song.shared_by` and calls `create_notification()` after adding a song to a playlist.
The rating path is simply missing the equivalent notification call.