"""
KJV Rightly Divided — backend.
Serves the static Bible + companion chart, and provides a small authenticated
notes API (per-user chapter and verse notes) backed by Postgres (SQLite locally).
"""
import os
import re
import json
import gzip
import datetime
from flask import Flask, request, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import UniqueConstraint

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder=None)

# --- Secret key (sign session cookies) ---
app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-change-me")

# --- Database URL: Railway injects DATABASE_URL for the Postgres plugin. ---
# Force the psycopg2 driver (the one pinned in requirements.txt). Railway/SQLAlchemy may hand
# us a bare "postgresql://" or a "postgresql+psycopg://" (psycopg v3) URL; psycopg v3 is NOT
# installed, so creating the engine crashed the worker on boot with
# "ModuleNotFoundError: No module named 'psycopg'". Rewriting any Postgres scheme to
# "+psycopg2" keeps us on the driver we actually ship.
db_url = os.environ.get("DATABASE_URL", "").strip()
for _pfx in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql://", "postgres://"):
    if db_url.startswith(_pfx):
        db_url = "postgresql+psycopg2://" + db_url[len(_pfx):]
        break
if not db_url:
    db_url = "sqlite:///" + os.path.join(BASE_DIR, "notes.db")   # local dev fallback
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# --- Cookie hardening (Secure only when not local dev) ---
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("DATABASE_URL"))

# Notes are rich text (HTML) and may embed images as data: URLs, so allow larger bodies.
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# Only these accounts may embed images in notes (everyone still gets text formatting).
ADMIN_EMAILS = {e.strip().lower() for e in
                os.environ.get("ADMIN_EMAILS", "gary@gkpsolutions.net").split(",") if e.strip()}
NOTE_MAX_CHARS = 8_000_000   # cap per-note HTML (a few embedded images)

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class Note(db.Model):
    __tablename__ = "notes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    book = db.Column(db.String(40), nullable=False)
    chapter = db.Column(db.Integer, nullable=False)
    verse = db.Column(db.Integer, nullable=True)          # NULL = chapter-level note
    text = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow,
                           onupdate=datetime.datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "book", "chapter", "verse",
                                       name="uq_note_scope"),)


class Highlight(db.Model):
    __tablename__ = "highlights"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    book = db.Column(db.String(40), nullable=False)
    chapter = db.Column(db.Integer, nullable=False)
    verse = db.Column(db.Integer, nullable=False)
    data = db.Column(db.Text, nullable=False, default="{}")   # JSON {wordIndex: colorCode}
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow,
                           onupdate=datetime.datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "book", "chapter", "verse",
                                       name="uq_highlight_scope"),)


class Xref(db.Model):
    __tablename__ = "xrefs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    book = db.Column(db.String(40), nullable=False)        # source verse
    chapter = db.Column(db.Integer, nullable=False)
    verse = db.Column(db.Integer, nullable=False)
    tbook = db.Column(db.String(40), nullable=False)       # target (referenced) verse
    tchapter = db.Column(db.Integer, nullable=False)
    tverse = db.Column(db.Integer, nullable=False)         # start verse
    tverse_end = db.Column(db.Integer, nullable=True)      # end of range; NULL = single verse
    note = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    __table_args__ = (db.Index("ix_xref_user_scope", "user_id", "book", "chapter"),)


class Topic(db.Model):
    __tablename__ = "topics"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    key = db.Column(db.String(80), nullable=False)         # curated slug, or a custom key
    title = db.Column(db.String(160), nullable=False, default="")
    body = db.Column(db.Text, nullable=False, default="")  # user's teaching notes
    verses = db.Column(db.Text, nullable=False, default="[]")  # JSON list of "Book C:V" refs
    is_custom = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow,
                           onupdate=datetime.datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_topic_scope"),)


def _migrate_xrefs():
    """Idempotent: add tverse_end and drop the old start-only unique constraint."""
    try:
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        if "xrefs" not in insp.get_table_names():
            return
        cols = [c["name"] for c in insp.get_columns("xrefs")]
        with db.engine.begin() as conn:
            if "tverse_end" not in cols:
                conn.execute(text("ALTER TABLE xrefs ADD COLUMN tverse_end INTEGER"))
            try:
                conn.execute(text("ALTER TABLE xrefs DROP CONSTRAINT IF EXISTS uq_xref_scope"))
            except Exception:
                pass  # SQLite can't drop constraints; harmless in dev
    except Exception:
        pass


def _xref_ref(tbook, tchapter, tverse, tverse_end):
    if tverse_end and tverse_end > tverse:
        return f"{tbook} {tchapter}:{tverse}-{tverse_end}"
    return f"{tbook} {tchapter}:{tverse}"


with app.app_context():
    try:
        db.create_all()
        _migrate_xrefs()
    except Exception as _dberr:
        # Never let a database hiccup at startup take the whole site down. The Bible
        # reading, contents and commentary work without the DB; the per-user features
        # (login, notes, highlights, cross-refs, topics) reconnect once the DB is back.
        print("WARN: database init skipped at startup:", _dberr, flush=True)


# ---------------- helpers ----------------
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return db.session.get(User, uid)


def require_login():
    u = current_user()
    if not u:
        return None
    return u


def _is_admin(u):
    return bool(u) and (u.email or "").strip().lower() in ADMIN_EMAILS


# --- Note rich-text (HTML) sanitizing -----------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPTISH_PAIR_RE = re.compile(r"(?is)<(script|style|iframe|object|embed)\b.*?</\1\s*>")
_SCRIPTISH_TAG_RE = re.compile(r"(?is)</?(script|style|iframe|object|embed|link|meta)\b[^>]*>")
_ON_ATTR_RE = re.compile(r"(?is)\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_JS_URL_RE = re.compile(r"(?is)(href|src)\s*=\s*(['\"])\s*javascript:[^'\"]*(['\"])")
_IMG_RE = re.compile(r"(?is)<img\b[^>]*>")


def _strip_tags(s):
    return _TAG_RE.sub("", s or "").replace("&nbsp;", " ")


def _clean_note_html(html, allow_img):
    """Remove scripts/handlers; strip <img> entirely unless the author may embed images."""
    if not html:
        return ""
    s = _SCRIPTISH_PAIR_RE.sub("", html)
    s = _SCRIPTISH_TAG_RE.sub("", s)
    s = _ON_ATTR_RE.sub("", s)
    s = _JS_URL_RE.sub(r"\1=\2\3", s)
    if not allow_img:
        s = _IMG_RE.sub("", s)
    return s.strip()


def _html_is_empty(html):
    if not html:
        return True
    if _IMG_RE.search(html):
        return False
    return _strip_tags(html).replace(" ", " ").strip() == ""


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------- auth ----------------
@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not EMAIL_RE.match(email):
        return jsonify(error="Please enter a valid email address."), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400
    if User.query.filter_by(email=email).first():
        return jsonify(error="An account with that email already exists."), 409
    user = User(email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    session["uid"] = user.id
    return jsonify(email=user.email)


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify(error="Incorrect email or password."), 401
    session["uid"] = user.id
    return jsonify(email=user.email)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = current_user()
    return jsonify(email=u.email if u else None, can_upload=_is_admin(u), is_admin=_is_admin(u))


# ---------------- notes ----------------
def _norm_verse(v):
    if v in (None, "", "null"):
        return None
    return int(v)


@app.get("/api/notes")
def get_notes():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    book = (request.args.get("book") or "").strip()
    try:
        chapter = int(request.args.get("chapter"))
    except (TypeError, ValueError):
        return jsonify(error="book and chapter are required."), 400
    rows = Note.query.filter_by(user_id=u.id, book=book, chapter=chapter).all()
    chapter_note = None
    verses = {}
    for r in rows:
        if r.verse is None:
            chapter_note = r.text
        else:
            verses[str(r.verse)] = r.text
    return jsonify(book=book, chapter=chapter, chapter_note=chapter_note, verses=verses)


@app.put("/api/notes")
def put_note():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    data = request.get_json(silent=True) or {}
    book = (data.get("book") or "").strip()
    try:
        chapter = int(data.get("chapter"))
    except (TypeError, ValueError):
        return jsonify(error="book and chapter are required."), 400
    verse = _norm_verse(data.get("verse"))
    text = (data.get("text") or "")
    if len(text) > NOTE_MAX_CHARS:
        return jsonify(error="That note is too large — try a smaller image."), 413
    text = _clean_note_html(text, allow_img=_is_admin(u))
    row = Note.query.filter_by(user_id=u.id, book=book, chapter=chapter, verse=verse).first()
    if _html_is_empty(text):
        if row:
            db.session.delete(row)
            db.session.commit()
        return jsonify(ok=True, deleted=True)
    if row:
        row.text = text
    else:
        row = Note(user_id=u.id, book=book, chapter=chapter, verse=verse, text=text)
        db.session.add(row)
    db.session.commit()
    return jsonify(ok=True, updated_at=row.updated_at.isoformat())


@app.delete("/api/notes")
def delete_note():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    data = request.get_json(silent=True) or {}
    book = (data.get("book") or "").strip()
    try:
        chapter = int(data.get("chapter"))
    except (TypeError, ValueError):
        return jsonify(error="book and chapter are required."), 400
    verse = _norm_verse(data.get("verse"))
    row = Note.query.filter_by(user_id=u.id, book=book, chapter=chapter, verse=verse).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify(ok=True)


# ---------------- highlights ----------------
@app.get("/api/highlights")
def get_highlights():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    book = (request.args.get("book") or "").strip()
    try:
        chapter = int(request.args.get("chapter"))
    except (TypeError, ValueError):
        return jsonify(error="book and chapter are required."), 400
    rows = Highlight.query.filter_by(user_id=u.id, book=book, chapter=chapter).all()
    verses = {}
    for r in rows:
        try:
            d = json.loads(r.data)
        except Exception:
            d = {}
        if d:
            verses[str(r.verse)] = d
    return jsonify(book=book, chapter=chapter, verses=verses)


@app.put("/api/highlights")
def put_highlight():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    data = request.get_json(silent=True) or {}
    book = (data.get("book") or "").strip()
    try:
        chapter = int(data.get("chapter"))
        verse = int(data.get("verse"))
    except (TypeError, ValueError):
        return jsonify(error="book, chapter and verse are required."), 400
    hl = data.get("data") or {}
    if not isinstance(hl, dict):
        hl = {}
    row = Highlight.query.filter_by(user_id=u.id, book=book, chapter=chapter, verse=verse).first()
    if not hl:
        if row:
            db.session.delete(row)
            db.session.commit()
        return jsonify(ok=True, deleted=True)
    payload = json.dumps({str(k): str(v) for k, v in hl.items()})
    if row:
        row.data = payload
    else:
        row = Highlight(user_id=u.id, book=book, chapter=chapter, verse=verse, data=payload)
        db.session.add(row)
    db.session.commit()
    return jsonify(ok=True)


# ---------------- user cross-references ----------------
@app.get("/api/xrefs")
def get_xrefs():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    book = (request.args.get("book") or "").strip()
    try:
        chapter = int(request.args.get("chapter"))
    except (TypeError, ValueError):
        return jsonify(error="book and chapter are required."), 400
    # Order by INSERTION order (id) within each source verse -- the user's own
    # sequence is meaningful. (Was: tchapter, tverse -- which reshuffled the list
    # by target chapter and ignored the target BOOK entirely.)
    rows = (Xref.query.filter_by(user_id=u.id, book=book, chapter=chapter)
            .order_by(Xref.verse, Xref.id).all())
    verses = {}
    for r in rows:
        verses.setdefault(str(r.verse), []).append({
            "id": r.id, "tbook": r.tbook, "tchapter": r.tchapter, "tverse": r.tverse,
            "tverse_end": r.tverse_end,
            "ref": _xref_ref(r.tbook, r.tchapter, r.tverse, r.tverse_end), "note": r.note or ""})
    return jsonify(book=book, chapter=chapter, verses=verses)


@app.post("/api/xrefs")
def add_xref():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    d = request.get_json(silent=True) or {}
    book = (d.get("book") or "").strip()
    tbook = (d.get("tbook") or "").strip()
    try:
        chapter = int(d.get("chapter")); verse = int(d.get("verse"))
        tchapter = int(d.get("tchapter")); tverse = int(d.get("tverse"))
    except (TypeError, ValueError):
        return jsonify(error="Source and target book/chapter/verse are required."), 400
    if not book or not tbook:
        return jsonify(error="Book is required."), 400
    tverse_end = d.get("tverse_end")
    try:
        tverse_end = int(tverse_end) if tverse_end not in (None, "", 0) else None
    except (TypeError, ValueError):
        tverse_end = None
    if tverse_end is not None and tverse_end <= tverse:
        tverse_end = None  # not a real range
    note = (d.get("note") or "").strip()[:2000]
    # Optional "id" = EDIT AN EXISTING ROW IN PLACE. Keeping the row (and its id)
    # is what keeps an edited cross-reference in its original slot in the list;
    # the old delete-then-insert always sent it to the bottom.
    xid = d.get("id")
    try:
        xid = int(xid) if xid not in (None, "", 0) else None
    except (TypeError, ValueError):
        xid = None
    row = Xref.query.filter_by(id=xid, user_id=u.id).first() if xid is not None else None
    if row is not None:
        row.book, row.chapter, row.verse = book, chapter, verse
        row.tbook, row.tchapter, row.tverse, row.tverse_end = tbook, tchapter, tverse, tverse_end
        row.note = note
    rev = None
    if row is None:
        row = Xref.query.filter_by(user_id=u.id, book=book, chapter=chapter, verse=verse,
                                   tbook=tbook, tchapter=tchapter, tverse=tverse,
                                   tverse_end=tverse_end).first()
        if row:
            row.note = note
        else:
            row = Xref(user_id=u.id, book=book, chapter=chapter, verse=verse,
                       tbook=tbook, tchapter=tchapter, tverse=tverse,
                       tverse_end=tverse_end, note=note)
            db.session.add(row)
            # --- Reciprocal link: also add target-start -> source, once. ---
            # Source is always a single verse, so the reverse points at this one
            # verse (range targets collapse to their first verse, tverse). Created
            # independently: later edits/deletes of either side do NOT touch the other.
            if not (tbook == book and tchapter == chapter and tverse == verse):
                already = Xref.query.filter_by(
                    user_id=u.id, book=tbook, chapter=tchapter, verse=tverse,
                    tbook=book, tchapter=chapter, tverse=verse).first()
                if already is None:
                    rev = Xref(user_id=u.id, book=tbook, chapter=tchapter, verse=tverse,
                               tbook=book, tchapter=chapter, tverse=verse,
                               tverse_end=None, note=note)
                    db.session.add(rev)
    db.session.commit()
    resp = dict(id=row.id, ref=_xref_ref(tbook, tchapter, tverse, tverse_end), note=note,
                tbook=tbook, tchapter=tchapter, tverse=tverse, tverse_end=tverse_end)
    if rev is not None:
        resp["reverse"] = dict(id=rev.id, book=tbook, chapter=tchapter, verse=tverse,
                               tbook=book, tchapter=chapter, tverse=verse, tverse_end=None,
                               ref=_xref_ref(book, chapter, verse, None), note=note)
    return jsonify(**resp)


@app.delete("/api/xrefs")
def del_xref():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    d = request.get_json(silent=True) or {}
    try:
        xid = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify(error="id is required."), 400
    row = Xref.query.filter_by(id=xid, user_id=u.id).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify(ok=True)


# ---------------- study topics (per-user notes + attached verses) ----------------
def _topic_row(r):
    try:
        vs = json.loads(r.verses)
        if not isinstance(vs, list):
            vs = []
    except Exception:
        vs = []
    return {"id": r.id, "key": r.key, "title": r.title or "", "body": r.body or "",
            "verses": vs, "is_custom": bool(r.is_custom)}


@app.get("/api/topics")
def get_topics():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    rows = Topic.query.filter_by(user_id=u.id).all()
    return jsonify(topics=[_topic_row(r) for r in rows])


@app.put("/api/topics")
def put_topic():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    d = request.get_json(silent=True) or {}
    key = (d.get("key") or "").strip()[:80]
    if not key:
        return jsonify(error="key is required."), 400
    title = (d.get("title") or "").strip()[:160]
    body = (d.get("body") or "")
    if len(body) > NOTE_MAX_CHARS:
        return jsonify(error="That note is too large — try a smaller image."), 413
    body = _clean_note_html(body, allow_img=_is_admin(u))
    verses = d.get("verses")
    if not isinstance(verses, list):
        verses = []
    verses = [str(v)[:60] for v in verses][:200]
    is_custom = bool(d.get("is_custom"))
    row = Topic.query.filter_by(user_id=u.id, key=key).first()
    # A curated topic with nothing saved (no body, no verses, not custom) is deleted.
    if row and not is_custom and _html_is_empty(body) and not verses:
        db.session.delete(row)
        db.session.commit()
        return jsonify(ok=True, deleted=True)
    if row:
        row.title = title or row.title
        row.body = body
        row.verses = json.dumps(verses)
        if is_custom:
            row.is_custom = True
    else:
        row = Topic(user_id=u.id, key=key, title=title, body=body,
                    verses=json.dumps(verses), is_custom=is_custom)
        db.session.add(row)
    db.session.commit()
    return jsonify(_topic_row(row))


@app.delete("/api/topics")
def del_topic():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    d = request.get_json(silent=True) or {}
    key = (d.get("key") or "").strip()
    row = Topic.query.filter_by(user_id=u.id, key=key).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify(ok=True)


# ---------------- global search over the user's own content ----------------
def _snippet(text, q, width=110):
    text = text or ""
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, i - 35)
    end = min(len(text), i + len(q) + 70)
    s = text[start:end]
    return ("…" if start > 0 else "") + s + ("…" if end < len(text) else "")


@app.get("/api/search")
def search_all():
    u = require_login()
    if not u:
        return jsonify(error="Not logged in."), 401
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(results=[], q=q)
    like = "%" + q.lower() + "%"
    out = []
    # notes: verse / chapter / book
    for n in (Note.query.filter_by(user_id=u.id)
              .filter(db.func.lower(Note.text).like(like)).limit(60).all()):
        if n.chapter == 0:
            label = f"{n.book} · book note"
            nav = {"type": "book", "book": n.book}
        elif n.verse is None:
            label = f"{n.book} {n.chapter} · chapter note"
            nav = {"type": "chapter", "book": n.book, "chapter": n.chapter}
        else:
            label = f"{n.book} {n.chapter}:{n.verse}"
            nav = {"type": "verse", "book": n.book, "chapter": n.chapter, "verse": n.verse}
        out.append({"kind": "note", "label": label, "snippet": _snippet(_strip_tags(n.text), q), "nav": nav})
    # topics: title or body
    for t in (Topic.query.filter_by(user_id=u.id)
              .filter(db.or_(db.func.lower(Topic.body).like(like),
                             db.func.lower(Topic.title).like(like))).limit(40).all()):
        out.append({"kind": "topic", "label": (t.title or t.key) + " · topic",
                    "snippet": _snippet(_strip_tags(t.body), q), "nav": {"type": "topic", "key": t.key}})
    # personal cross-reference notes
    for x in (Xref.query.filter_by(user_id=u.id)
              .filter(db.func.lower(Xref.note).like(like)).limit(40).all()):
        label = f"{x.book} {x.chapter}:{x.verse} → {_xref_ref(x.tbook, x.tchapter, x.tverse, x.tverse_end)}"
        out.append({"kind": "xref", "label": label, "snippet": _snippet(x.note, q),
                    "nav": {"type": "verse", "book": x.book, "chapter": x.chapter, "verse": x.verse}})
    return jsonify(results=out[:80], q=q)


# ---------------- Strong's concordance ----------------
STRONGS_DIR = os.path.join(BASE_DIR, "data")


def _load_strongs(name):
    """Load a data file, preferring a gzipped .gz version if present."""
    gz = os.path.join(STRONGS_DIR, name + ".gz")
    plain = os.path.join(STRONGS_DIR, name)
    if os.path.exists(gz):
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(plain, encoding="utf-8") as f:
        return json.load(f)


try:
    STRONGS_VERSES = _load_strongs("strongs_verses.json")
    STRONGS_LEX = _load_strongs("strongs_lex.json")
except Exception:
    STRONGS_VERSES, STRONGS_LEX = {}, {}

TAG_RE = re.compile(r"\[([GH]\d+)\]")

# Webster's 1828 (public domain), filtered to KJV vocabulary. word -> {h: headword, d: definition}
try:
    WEBSTER = _load_strongs("webster1828.json")
except Exception:
    WEBSTER = {}

WORD_RE = re.compile(r"[A-Za-z']+")


def _norm_word(tok):
    w = tok.lower()
    if w.endswith("'s"):
        w = w[:-2]
    return w.strip("'")


@app.get("/api/define")
def define():
    book = (request.args.get("book") or "").strip()
    try:
        chapter = int(request.args.get("chapter"))
        verse = int(request.args.get("verse"))
    except (TypeError, ValueError):
        return jsonify(error="book, chapter and verse are required."), 400
    en = STRONGS_VERSES.get(f"{book}|{chapter}|{verse}")
    if en is None:
        return jsonify(ref=f"{book} {chapter}:{verse}", available=False, words=[])
    text = TAG_RE.sub("", en)
    words, seen = [], set()
    for tok in WORD_RE.findall(text):
        w = _norm_word(tok)
        if len(w) < 2 or w in seen:
            continue
        e = WEBSTER.get(w)
        if e:
            seen.add(w)
            words.append({"w": tok, "h": e["h"], "d": e["d"]})
    return jsonify(ref=f"{book} {chapter}:{verse}", available=True, words=words)

# Reverse concordance index: Strong's number -> list of verse refs where it occurs.
# Built once at startup from the tagged text already in memory (no extra data files).
STRONGS_INDEX = {}
for _ref, _en in STRONGS_VERSES.items():
    for _n in set(TAG_RE.findall(_en)):
        STRONGS_INDEX.setdefault(_n, []).append(_ref)


@app.get("/api/occurrences")
def occurrences():
    num = (request.args.get("num") or "").strip().upper()
    refs = STRONGS_INDEX.get(num, [])
    total = len(refs)
    limit = 500
    e = STRONGS_LEX.get(num) or {}
    items = []
    for r in refs[:limit]:
        b, c, v = r.rsplit("|", 2)
        items.append({"book": b, "chapter": int(c), "verse": int(v), "ref": f"{b} {c}:{v}"})
    return jsonify(num=num, orig=e.get("o", ""), translit=e.get("t", ""),
                   total=total, shown=len(items), items=items)


@app.get("/api/strongs")
def strongs():
    book = (request.args.get("book") or "").strip()
    try:
        chapter = int(request.args.get("chapter"))
        verse = int(request.args.get("verse"))
    except (TypeError, ValueError):
        return jsonify(error="book, chapter and verse are required."), 400
    en = STRONGS_VERSES.get(f"{book}|{chapter}|{verse}")
    if en is None:
        return jsonify(ref=f"{book} {chapter}:{verse}", available=False, words=[])
    words = []
    in_em = False
    for tok in en.split(" "):
        supplied = in_em or ("<em>" in tok)
        if "<em>" in tok:
            in_em = True
        nums = TAG_RE.findall(tok)
        word = TAG_RE.sub("", tok).replace("<em>", "").replace("</em>", "").strip()
        if "</em>" in tok:
            in_em = False
        if not word or not any(ch.isalpha() for ch in word):
            continue
        entries = []
        for n in nums:
            e = STRONGS_LEX.get(n)
            entries.append({"num": n, **e} if e else {"num": n})
        item = {"w": word, "entries": entries}
        if not entries:
            # No Strong's number: either a translator-supplied word (KJV italics)
            # or an English connective/article with no separate original-language word.
            item["supplied"] = bool(supplied)
        words.append(item)
    return jsonify(ref=f"{book} {chapter}:{verse}", available=True, words=words)


# ---------------- static site ----------------
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/chart.html")
def chart():
    return send_from_directory(BASE_DIR, "chart.html")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
