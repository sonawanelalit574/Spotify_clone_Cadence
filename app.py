
import os
import re
import uuid
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, abort, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# --------------------------------------------------------------------------
# App configuration
# --------------------------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
COVER_FOLDER = os.path.join(BASE_DIR, "static", "covers")
ALLOWED_AUDIO_EXT = {"mp3", "wav", "ogg", "m4a", "flac"}
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}
MAX_CONTENT_LENGTH = 60 * 1024 * 1024  # 60 MB per upload

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COVER_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("CADENCE_SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "instance", "cadence.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to keep listening."
login_manager.login_message_category = "info"


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

playlist_songs = db.Table(
    "playlist_songs",
    db.Column("playlist_id", db.Integer, db.ForeignKey("playlist.id"), primary_key=True),
    db.Column("song_id", db.Integer, db.ForeignKey("song.id"), primary_key=True),
    db.Column("position", db.Integer, default=0),
)

likes = db.Table(
    "likes",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("song_id", db.Integer, db.ForeignKey("song.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    songs = db.relationship("Song", backref="uploader", lazy=True, cascade="all, delete-orphan")
    playlists = db.relationship("Playlist", backref="owner", lazy=True, cascade="all, delete-orphan")
    liked_songs = db.relationship("Song", secondary=likes, backref="liked_by", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def initials(self):
        name = self.display_name or self.username
        parts = name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return name[:2].upper()


class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(200), default="Unknown Artist")
    album = db.Column(db.String(200), default="")
    source_type = db.Column(db.String(20), default="upload")  # 'upload' or 'youtube'
    filename = db.Column(db.String(300))          # for uploads
    youtube_id = db.Column(db.String(30))         # for youtube tracks
    cover_filename = db.Column(db.String(300))
    duration = db.Column(db.Integer, default=0)   # seconds
    play_count = db.Column(db.Integer, default=0)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        if self.source_type == "youtube":
            stream_url = None
            cover = f"https://img.youtube.com/vi/{self.youtube_id}/hqdefault.jpg"
        else:
            stream_url = url_for("stream", filename=self.filename)
            cover = url_for("static", filename=f"covers/{self.cover_filename}") if self.cover_filename else None
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "source_type": self.source_type,
            "youtube_id": self.youtube_id,
            "stream_url": stream_url,
            "cover": cover,
            "duration": self.duration,
            "uploader": self.uploader.display_name or self.uploader.username,
        }


class Playlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(300), default="")
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    songs = db.relationship(
        "Song", secondary=playlist_songs,
        order_by=playlist_songs.c.position,
        backref="playlists", lazy="dynamic"
    )


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def allowed_file(filename, allowed_set):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_set


def extract_youtube_id(url_or_id):
    """Accept a full YouTube URL, short url, or a bare 11-char video id."""
    url_or_id = url_or_id.strip()
    patterns = [
        r"(?:youtube\.com\/watch\?v=|youtube\.com\/embed\/|youtu\.be\/|youtube\.com\/shorts\/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    return None


def read_audio_metadata(filepath, fallback_title):
    """Best-effort ID3/metadata extraction using mutagen; never raises."""
    title, artist, album, duration = fallback_title, "Unknown Artist", "", 0
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath, easy=True)
        if audio is not None:
            if audio.tags:
                title = (audio.tags.get("title") or [title])[0]
                artist = (audio.tags.get("artist") or [artist])[0]
                album = (audio.tags.get("album") or [album])[0]
            if audio.info and getattr(audio.info, "length", None):
                duration = int(audio.info.length)
    except Exception:
        pass
    return title, artist, album, duration


def get_or_create_liked_playlist(user):
    """Liked Songs isn't a real Playlist row; it's derived from `likes`."""
    return user.liked_songs


# --------------------------------------------------------------------------
# Auth routes
# --------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip() or username
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not email or not password:
            flash("All fields are required.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords don't match.", "error")
        elif User.query.filter_by(username=username).first():
            flash("That username is taken.", "error")
        elif User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
        else:
            user = User(username=username, email=email, display_name=display_name)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f"Welcome to Cadence, {user.display_name}!", "success")
            return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier.lower())
        ).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            flash(f"Welcome back, {user.display_name or user.username}.", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        flash("Incorrect username/email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been logged out. See you soon!", "info")
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Core app routes
# --------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        songs = Song.query.filter(
            (Song.title.ilike(like)) | (Song.artist.ilike(like)) | (Song.album.ilike(like))
        ).order_by(Song.created_at.desc()).all()
    else:
        songs = Song.query.order_by(Song.created_at.desc()).limit(60).all()

    my_songs = Song.query.filter_by(uploader_id=current_user.id).order_by(Song.created_at.desc()).all()
    playlists = Playlist.query.filter_by(owner_id=current_user.id).order_by(Playlist.created_at.desc()).all()

    return render_template(
        "index.html",
        songs=songs, my_songs=my_songs, playlists=playlists,
        query=q, liked_ids={s.id for s in current_user.liked_songs},
    )


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("audio_file")
        cover = request.files.get("cover_file")

        if not file or file.filename == "":
            flash("Please choose an audio file to upload.", "error")
            return redirect(url_for("upload"))

        if not allowed_file(file.filename, ALLOWED_AUDIO_EXT):
            flash("Unsupported audio format. Use mp3, wav, ogg, m4a or flac.", "error")
            return redirect(url_for("upload"))

        ext = file.filename.rsplit(".", 1)[1].lower()
        stored_name = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)
        file.save(path)

        fallback_title = secure_filename(file.filename.rsplit(".", 1)[0]) or "Untitled"
        title, artist, album, duration = read_audio_metadata(path, fallback_title)

        # Manual form fields override auto-detected metadata when provided
        title = request.form.get("title", "").strip() or title
        artist = request.form.get("artist", "").strip() or artist
        album = request.form.get("album", "").strip() or album

        cover_filename = None
        if cover and cover.filename and allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
            cover_ext = cover.filename.rsplit(".", 1)[1].lower()
            cover_filename = f"{uuid.uuid4().hex}.{cover_ext}"
            cover.save(os.path.join(COVER_FOLDER, cover_filename))

        song = Song(
            title=title, artist=artist, album=album,
            source_type="upload", filename=stored_name,
            cover_filename=cover_filename, duration=duration,
            uploader_id=current_user.id,
        )
        db.session.add(song)
        db.session.commit()
        flash(f'"{song.title}" uploaded and ready to play!', "success")
        return redirect(url_for("index"))

    return render_template("upload.html")


@app.route("/add-youtube", methods=["GET", "POST"])
@login_required
def add_youtube():
    if request.method == "POST":
        url = request.form.get("youtube_url", "").strip()
        title = request.form.get("title", "").strip()
        artist = request.form.get("artist", "").strip() or "YouTube"

        video_id = extract_youtube_id(url)
        if not video_id:
            flash("Couldn't find a valid YouTube video in that link.", "error")
            return redirect(url_for("add_youtube"))

        song = Song(
            title=title or "YouTube track",
            artist=artist, album="",
            source_type="youtube", youtube_id=video_id,
            uploader_id=current_user.id,
        )
        db.session.add(song)
        db.session.commit()
        flash(f'"{song.title}" added from YouTube!', "success")
        return redirect(url_for("index"))

    return render_template("add_youtube.html")


@app.route("/stream/<path:filename>")
@login_required
def stream(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, conditional=True)


@app.route("/song/<int:song_id>/delete", methods=["POST"])
@login_required
def delete_song(song_id):
    song = Song.query.get_or_404(song_id)
    if song.uploader_id != current_user.id:
        abort(403)
    if song.source_type == "upload" and song.filename:
        try:
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], song.filename))
        except OSError:
            pass
    db.session.delete(song)
    db.session.commit()
    flash("Track removed from your library.", "info")
    return redirect(url_for("index"))


@app.route("/song/<int:song_id>/played", methods=["POST"])
@login_required
def mark_played(song_id):
    song = Song.query.get_or_404(song_id)
    song.play_count = (song.play_count or 0) + 1
    db.session.commit()
    return jsonify({"ok": True, "play_count": song.play_count})


@app.route("/api/queue")
@login_required
def api_queue():
    """Returns the current filtered/browsed song list as JSON for the player."""
    scope = request.args.get("scope", "all")
    q = request.args.get("q", "").strip()

    if scope == "mine":
        songs = Song.query.filter_by(uploader_id=current_user.id).order_by(Song.created_at.desc()).all()
    elif scope == "liked":
        songs = current_user.liked_songs.order_by(Song.created_at.desc()).all() \
            if hasattr(current_user.liked_songs, "order_by") else list(current_user.liked_songs)
    elif scope.startswith("playlist:"):
        pid = int(scope.split(":", 1)[1])
        playlist = Playlist.query.get_or_404(pid)
        songs = playlist.songs.all()
    elif q:
        like = f"%{q}%"
        songs = Song.query.filter(
            (Song.title.ilike(like)) | (Song.artist.ilike(like)) | (Song.album.ilike(like))
        ).order_by(Song.created_at.desc()).all()
    else:
        songs = Song.query.order_by(Song.created_at.desc()).limit(60).all()

    return jsonify([s.to_dict() for s in songs])


# --------------------------------------------------------------------------
# Likes
# --------------------------------------------------------------------------

@app.route("/like/<int:song_id>", methods=["POST"])
@login_required
def toggle_like(song_id):
    song = Song.query.get_or_404(song_id)
    if song in current_user.liked_songs:
        current_user.liked_songs.remove(song)
        liked = False
    else:
        current_user.liked_songs.append(song)
        liked = True
    db.session.commit()
    return jsonify({"ok": True, "liked": liked})


@app.route("/liked")
@login_required
def liked_songs_page():
    songs = list(current_user.liked_songs)
    return render_template("liked.html", songs=songs, liked_ids={s.id for s in songs})


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------

@app.route("/playlists/create", methods=["POST"])
@login_required
def create_playlist():
    name = request.form.get("name", "").strip() or "New Playlist"
    description = request.form.get("description", "").strip()
    playlist = Playlist(name=name, description=description, owner_id=current_user.id)
    db.session.add(playlist)
    db.session.commit()
    flash(f'Playlist "{playlist.name}" created.', "success")
    return redirect(url_for("view_playlist", playlist_id=playlist.id))


@app.route("/playlist/<int:playlist_id>")
@login_required
def view_playlist(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    if playlist.owner_id != current_user.id:
        abort(403)
    songs = playlist.songs.all()
    all_songs = Song.query.order_by(Song.created_at.desc()).all()
    return render_template(
        "playlist.html", playlist=playlist, songs=songs, all_songs=all_songs,
        liked_ids={s.id for s in current_user.liked_songs},
    )


@app.route("/playlist/<int:playlist_id>/add/<int:song_id>", methods=["POST"])
@login_required
def add_to_playlist(playlist_id, song_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    if playlist.owner_id != current_user.id:
        abort(403)
    song = Song.query.get_or_404(song_id)
    if song not in playlist.songs:
        next_pos = playlist.songs.count()
        db.session.execute(
            playlist_songs.insert().values(playlist_id=playlist.id, song_id=song.id, position=next_pos)
        )
        db.session.commit()
        flash(f'Added "{song.title}" to {playlist.name}.', "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/playlist/<int:playlist_id>/remove/<int:song_id>", methods=["POST"])
@login_required
def remove_from_playlist(playlist_id, song_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    if playlist.owner_id != current_user.id:
        abort(403)
    song = Song.query.get_or_404(song_id)
    if song in playlist.songs:
        playlist.songs.remove(song)
        db.session.commit()
    return redirect(url_for("view_playlist", playlist_id=playlist.id))


@app.route("/playlist/<int:playlist_id>/delete", methods=["POST"])
@login_required
def delete_playlist(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    if playlist.owner_id != current_user.id:
        abort(403)
    db.session.delete(playlist)
    db.session.commit()
    flash("Playlist deleted.", "info")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------

@app.route("/profile")
@login_required
def profile():
    song_count = Song.query.filter_by(uploader_id=current_user.id).count()
    playlist_count = Playlist.query.filter_by(owner_id=current_user.id).count()
    liked_count = current_user.liked_songs.count() if hasattr(current_user.liked_songs, "count") else len(list(current_user.liked_songs))
    return render_template(
        "profile.html", song_count=song_count,
        playlist_count=playlist_count, liked_count=liked_count,
    )


# --------------------------------------------------------------------------
# Error handlers
# --------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You don't have access to that."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="That page doesn't exist."), 404


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413, message="That file is too large (60MB max)."), 413


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
