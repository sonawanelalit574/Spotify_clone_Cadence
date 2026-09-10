# Cadence 🎵

A full-featured, Spotify-style music streaming web app built with **Flask**. Create an
account, upload your own audio files, pull in tracks straight from YouTube links, build
playlists, like songs, search your library, and play everything through one unified
player bar.

## Features

- **Accounts** — register / log in / log out with securely hashed passwords (Flask-Login + Werkzeug)
- **Upload music** — MP3, WAV, OGG, M4A, FLAC (up to 60MB). Title/artist/album/duration are
  auto-read from the file's tags via `mutagen`, with optional manual overrides and a custom cover image
- **YouTube tracks** — paste any YouTube link and it becomes a playable track in your library,
  streamed through the official YouTube IFrame Player API
- **One player, two sources** — the bottom player bar plays uploaded audio via HTML5 `<audio>`
  and YouTube tracks via a hidden YouTube player, with the exact same play/pause, next/previous,
  seek, volume, shuffle and repeat controls for both
- **Playlists** — create, add/remove tracks, delete
- **Liked Songs** — one-click heart to save any track
- **Search** — across title, artist, and album
- **Profile page** — quick stats on your uploads, playlists and likes
- **Distinct, hand-built UI** — dark "midnight ink" theme with a warm gold/violet accent,
  Space Grotesk + Inter type, fully responsive down to mobile

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000**, register an account, and start uploading.

The SQLite database is created automatically on first run at `instance/cadence.db`.
Uploaded audio files are stored in `uploads/`, and cover art in `static/covers/`.

## Project structure

```
app.py                  Flask app: models, routes, auth, upload & YouTube logic
templates/
  base.html              Shell: sidebar, topbar, global player bar
  index.html             Home / discover feed + your uploads
  login.html / register.html
  upload.html            Upload form with drag-and-drop
  add_youtube.html       Add-from-YouTube form
  liked.html             Liked Songs
  playlist.html          Single playlist view + "add tracks" picker
  profile.html
  error.html
  _macros.html           Shared song-row rendering macro
static/
  css/style.css          Full design system
  js/player.js           Unified HTML5 + YouTube player engine
uploads/                 Uploaded audio files (gitignore this in production)
requirements.txt
```

## Notes for production use

- Set a real `CADENCE_SECRET_KEY` environment variable instead of the dev default.
- Swap SQLite for Postgres/MySQL by changing `SQLALCHEMY_DATABASE_URI`.
- Put uploads behind a CDN or object storage (S3, GCS) instead of local disk at scale.
- Add rate limiting / file-type sniffing (not just extension checks) before accepting uploads
  in any internet-facing deployment.
- YouTube tracks stream via YouTube's own player, so playback stays compliant with YouTube's
  Terms of Service — Cadence never downloads or rehosts YouTube audio/video.
