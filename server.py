#!/usr/bin/env python3
"""Story Studio: a small collaborative workspace for a YouTube storytelling team.

Pure standard library (http.server + sqlite3). Run:  python3 server.py
"""
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import ai

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
DB_PATH = os.environ.get("STUDIO_DB", str(ROOT / "studio.db"))
STATUSES = ["idea", "research", "outline", "script", "review", "filmed", "published"]
SESSION_TTL = 30 * 24 * 3600
VERSION_MIN_GAP = 120  # seconds between automatic version snapshots
MAX_BODY = 5 * 1024 * 1024

# ---- deployment settings (environment variables, read when used) ----
#   STUDIO_HOST / STUDIO_PORT   where to listen (default 127.0.0.1:8000)
#   STUDIO_DB                   path of the SQLite file
#   STUDIO_TRUSTED_PROXY        comma-separated addresses of a reverse proxy whose X-Forwarded-For /
#                               X-Forwarded-Proto headers are believed (default: 127.0.0.1,::1)
#   STUDIO_SETUP_TOKEN          if set, creating the very first admin account requires this token
#   STUDIO_COOKIE_SECURE=1      always mark the session cookie Secure (otherwise: only over HTTPS)
LOGIN_WINDOW = 15 * 60
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def trusted_proxies():
    raw = os.environ.get("STUDIO_TRUSTED_PROXY", "127.0.0.1,::1")
    return {a.strip() for a in raw.split(",") if a.strip()}


def norm_ip(addr):
    addr = (addr or "").strip()
    return addr[7:] if addr.lower().startswith("::ffff:") else addr


class Limiter:
    """Counts failures per key inside a sliding window; blocks a key once it reaches the limit."""

    def __init__(self, limit, window=LOGIN_WINDOW, cap=20000):
        self.limit, self.window, self.cap = limit, window, cap
        self.hits, self.lock = {}, threading.Lock()

    def _prune(self, key, now):
        q = self.hits.get(key)
        if q is not None:
            while q and q[0] <= now - self.window:
                q.popleft()
            if not q:
                del self.hits[key]
                return None
        return q

    def wait(self, key):
        """Seconds until this key may try again (0 when it is not blocked)."""
        now = time.time()
        with self.lock:
            q = self._prune(key, now)
            if q and len(q) >= self.limit:
                return int(q[len(q) - self.limit] + self.window - now) + 1
        return 0

    def fail(self, key):
        now = time.time()
        with self.lock:
            if len(self.hits) > self.cap:  # memory guard against floods of random keys
                self.hits.clear()
            self.hits.setdefault(key, deque()).append(now)


LOGIN_BY_IP, LOGIN_BY_USER, SETUP_BY_IP = Limiter(10), Limiter(20), Limiter(5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS videos(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idea',
  owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL, deadline TEXT DEFAULT '',
  notes TEXT DEFAULT '', content TEXT NOT NULL DEFAULT '', rev INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, content TEXT NOT NULL,
  label TEXT DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, body TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS examples(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai_runs(
  id INTEGER PRIMARY KEY, video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, stage TEXT NOT NULL,
  input TEXT, output TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = db()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ai_runs)")}
    for name, typ in (("model", "TEXT"), ("in_tokens", "INTEGER"), ("out_tokens", "INTEGER"),
                      ("cache_read", "INTEGER"), ("cache_write", "INTEGER"), ("cost_usd", "REAL")):
        if name not in cols:  # older databases: add usage columns; old rows keep NULLs
            conn.execute(f"ALTER TABLE ai_runs ADD COLUMN {name} {typ}")
    conn.commit()
    seed_defaults(conn)
    conn.commit()
    conn.close()


# ---------- helpers ----------

def hash_pw(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${h.hex()}"


def check_pw(password, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(password, salt), stored)


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def row(r):
    return dict(r) if r else None


class HttpError(Exception):
    def __init__(self, code, message, headers=None, extra=None):
        self.code, self.message, self.headers, self.extra = code, message, headers, extra


OLD_STYLE_GUIDE_EN = """CHANNEL: (name, niche, audience)

VOICE
- Tone: (e.g. calm, curious, a little dry humour)
- Point of view: (e.g. second person "you", or a narrator)
- Sentences: short and spoken. Vary rhythm. One idea per sentence.

HOOKS
- Open in the middle of the action or with the most surprising fact. No "Hey guys" intro.
- Within 30 seconds the viewer must know what question the story will answer.

PACING
- Raise a new question every 60-90 seconds.
- Pay off the biggest question late, then end quickly.

USE
- (phrases, devices and transitions you like)

AVOID
- (cliches, filler words, phrases you hate)
"""

OLD_TEMPLATE_EN = """HOOK (0:00-0:30)
[VISUAL: ]


SETUP - who, where, what is normal


RISING TENSION - beat 1


RISING TENSION - beat 2


TURNING POINT


PAYOFF


OUTRO / CTA
"""


STARTER_STYLE_GUIDE = """CHANNEL: (nama channel, topik, penonton)

BAHASA DAN SAPAAN
- Bahasa Indonesia baku, enak didengar. Hindari bahasa gaul seperti gue/lo.
- Sapaan: (tulis satu: aku/kamu atau saya/Anda). Pakai konsisten dari awal sampai akhir.

SUARA
- Nada: (misalnya tenang, penasaran, sedikit humor kering)
- Kalimat pendek dan lisan. Variasikan ritme. Satu gagasan per kalimat.

PEMBUKA
- Mulai di tengah kejadian atau dengan fakta paling mengejutkan. Tanpa basa-basi sapaan panjang.
- Dalam 30 detik pertama, penonton harus tahu pertanyaan apa yang akan dijawab cerita ini.

TEMPO
- Munculkan pertanyaan baru setiap 60-90 detik.
- Jawab pertanyaan terbesar menjelang akhir, lalu tutup dengan cepat.

DIPAKAI
- (frasa, gaya, dan transisi yang disukai)

DIHINDARI
- (klise, kata pengisi, frasa yang tidak disukai)
"""

STARTER_TEMPLATE = """PEMBUKA (0:00-0:30)
[VISUAL: ]


LATAR - siapa, di mana, apa yang normal


KETEGANGAN - bagian 1


KETEGANGAN - bagian 2


TITIK BALIK


PENUTUP
"""

def seed_defaults(conn):
    if not conn.execute("SELECT 1 FROM settings WHERE key='style_guide'").fetchone():
        set_setting(conn, "style_guide", STARTER_STYLE_GUIDE)
        set_setting(conn, "script_template", STARTER_TEMPLATE)
        return
    # Existing database: replace the old English starter text only if nobody has edited it.
    if get_setting(conn, "style_guide") == OLD_STYLE_GUIDE_EN:
        set_setting(conn, "style_guide", STARTER_STYLE_GUIDE)
    if get_setting(conn, "script_template") == OLD_TEMPLATE_EN:
        set_setting(conn, "script_template", STARTER_TEMPLATE)


# ---------- handler ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "StoryStudio/1.0"

    def log_message(self, fmt, *args):
        pass  # no per-request access log

    def log_error(self, fmt, *args):  # unexpected server errors do go to stderr (systemd journal)
        print(time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args if args else fmt, file=sys.stderr, flush=True)

    def client_ip(self):
        """Address of the client. X-Forwarded-For is only believed when the request comes from a trusted proxy."""
        peer = norm_ip(self.client_address[0])
        trusted = trusted_proxies()
        if peer in trusted:
            for part in reversed((self.headers.get("X-Forwarded-For") or "").split(",")):
                ip = norm_ip(part)
                if ip and ip not in trusted:
                    return ip
        return peer

    def is_https(self):
        if os.environ.get("STUDIO_COOKIE_SECURE") == "1":
            return True
        return (norm_ip(self.client_address[0]) in trusted_proxies()
                and (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower() == "https")

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        if self.is_https():
            self.send_header("Strict-Transport-Security", "max-age=15552000")

    # --- plumbing ---
    def _send(self, code, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise HttpError(413, "Request too large")
        if length == 0:
            return {}
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise HttpError(415, "Content-Type must be application/json")
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            raise HttpError(400, "Invalid JSON")
        if not isinstance(data, dict):
            raise HttpError(400, "JSON object expected")
        return data

    def _user(self, conn):
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        if "sid" not in cookie:
            return None
        r = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=? AND s.expires>?",
            (cookie["sid"].value, time.time()),
        ).fetchone()
        return r

    def _dispatch(self, method):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            if method != "GET":
                raise HttpError(405, "Method not allowed")
            return self._static(path)
        conn = db()
        try:
            for m, pattern, fn, public in ROUTES:
                if m != method:
                    continue
                match = pattern.fullmatch(path)
                if not match:
                    continue
                user = self._user(conn)
                if not public and not user:
                    raise HttpError(401, "Not signed in")
                body = self._json_body() if method in ("POST", "PUT", "PATCH") else {}
                result = fn(self, conn, user, body, *match.groups())
                conn.commit()
                if isinstance(result, tuple):
                    return self._send(result[0], result[1], result[2] if len(result) > 2 else None)
                return self._send(200, result)
            raise HttpError(404, "Not found")
        finally:
            conn.close()

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        if path == "/panduan.md":  # the user guide lives at the repo root so GitHub shows it; one source for both
            target = ROOT / "PANDUAN.md"
        else:
            target = (STATIC / path.lstrip("/")).resolve()
            if STATIC.resolve() not in target.parents:
                raise HttpError(404, "Not found")
        if not target.is_file():
            raise HttpError(404, "Not found")
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        try:
            self._dispatch(method)
        except HttpError as e:
            self._send(e.code, {"error": e.message, **(e.extra or {})}, e.headers)
        except ai.AIError as e:
            self._send(502, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa
            self.log_error("error: %r", e)
            self._send(500, {"error": "Internal server error"})

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PUT(self): self._handle("PUT")
    def do_PATCH(self): self._handle("PATCH")
    def do_DELETE(self): self._handle("DELETE")


# ---------- route functions: fn(handler, conn, user, body, *groups) ----------

ROUTES = []


def route(method, pattern, public=False):
    def deco(fn):
        ROUTES.append((method, re.compile(pattern), fn, public))
        return fn
    return deco


def need_admin(user):
    if not user["is_admin"]:
        raise HttpError(403, "Admin only")


def public_user(u):
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "is_admin": bool(u["is_admin"])}


def start_session(conn, user_id, h=None):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)",
                 (token, user_id, time.time() + SESSION_TTL))
    secure = "; Secure" if h is not None and h.is_https() else ""
    return {"Set-Cookie": f"sid={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL}{secure}"}


def clean(s, maxlen, name):
    s = (s or "").strip() if isinstance(s, str) else ""
    if len(s) > maxlen:
        raise HttpError(400, f"{name} too long")
    return s


@route("GET", r"/api/state", public=True)
def api_state(h, conn, user, body):
    n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    return {"needs_setup": n == 0, "needs_token": n == 0 and bool(os.environ.get("STUDIO_SETUP_TOKEN")),
            "user": public_user(user) if user else None, "statuses": STATUSES}


@route("POST", r"/api/setup", public=True)
def api_setup(h, conn, user, body):
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] > 0:
        raise HttpError(403, "Already set up")
    expected = os.environ.get("STUDIO_SETUP_TOKEN", "")
    if expected:
        ip = h.client_ip()
        wait = SETUP_BY_IP.wait(ip)
        if wait:
            raise HttpError(429, "Too many attempts", {"Retry-After": str(wait)}, {"retry_after": wait})
        given = body.get("setup_token") if isinstance(body.get("setup_token"), str) else ""
        if not hmac.compare_digest(given.encode(), expected.encode()):
            SETUP_BY_IP.fail(ip)
            h.log_error("setup: wrong setup token from %s", ip)
            raise HttpError(403, "Invalid setup token")
    return create_user(conn, body, admin=True, login=True, h=h)


def create_user(conn, body, admin=False, login=False, h=None):
    username = clean(body.get("username"), 40, "Username").lower()
    name = clean(body.get("display_name"), 60, "Name") or username
    password = body.get("password") or ""
    if not re.fullmatch(r"[a-z0-9_.-]{2,40}", username):
        raise HttpError(400, "Username: 2-40 chars, letters/numbers/._-")
    if len(password) < 8:
        raise HttpError(400, "Password must be at least 8 characters")
    try:
        cur = conn.execute(
            "INSERT INTO users(username,display_name,pw_hash,is_admin,created) VALUES(?,?,?,?,?)",
            (username, name, hash_pw(password), 1 if admin else 0, time.time()))
    except sqlite3.IntegrityError:
        raise HttpError(409, "Username already taken")
    u = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    headers = start_session(conn, u["id"], h) if login else None
    return (200, {"user": public_user(u)}, headers)


@route("POST", r"/api/login", public=True)
def api_login(h, conn, user, body):
    ip = h.client_ip()
    uname = (body.get("username") if isinstance(body.get("username"), str) else "").strip().lower()[:80]
    wait = max(LOGIN_BY_IP.wait(ip), LOGIN_BY_USER.wait(uname))
    if wait:  # checked before any password hashing, so blocked attempts cost almost nothing
        raise HttpError(429, "Too many attempts", {"Retry-After": str(wait)}, {"retry_after": wait})
    u = conn.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
    pw = body.get("password") if isinstance(body.get("password"), str) else ""
    if not u or not check_pw(pw, u["pw_hash"]):
        LOGIN_BY_IP.fail(ip)
        LOGIN_BY_USER.fail(uname)
        h.log_error("login: failed for %r from %s", uname[:40], ip)
        time.sleep(0.5)
        raise HttpError(401, "Wrong username or password")
    conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
    return (200, {"user": public_user(u)}, start_session(conn, u["id"], h))


@route("POST", r"/api/logout", public=True)
def api_logout(h, conn, user, body):
    cookie = SimpleCookie(h.headers.get("Cookie") or "")
    if "sid" in cookie:
        conn.execute("DELETE FROM sessions WHERE token=?", (cookie["sid"].value,))
    return (200, {"ok": True}, {"Set-Cookie": "sid=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
                                + ("; Secure" if h.is_https() else "")})


# --- users ---
@route("GET", r"/api/users")
def api_users(h, conn, user, body):
    return [public_user(u) for u in conn.execute("SELECT * FROM users ORDER BY display_name")]


@route("POST", r"/api/users")
def api_users_create(h, conn, user, body):
    need_admin(user)
    code, payload, _ = create_user(conn, body, admin=bool(body.get("is_admin")))
    return code, payload


@route("DELETE", r"/api/users/(\d+)")
def api_users_delete(h, conn, user, body, uid):
    need_admin(user)
    if int(uid) == user["id"]:
        raise HttpError(400, "You cannot delete yourself")
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@route("POST", r"/api/password")
def api_password(h, conn, user, body):
    if not check_pw(body.get("current") or "", user["pw_hash"]):
        raise HttpError(403, "Current password is wrong")
    if len(body.get("new") or "") < 8:
        raise HttpError(400, "Password must be at least 8 characters")
    conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_pw(body["new"]), user["id"]))
    return {"ok": True}


# --- videos ---
VIDEO_LIST_SQL = ("SELECT v.id,v.title,v.status,v.owner_id,v.deadline,v.updated,"
                  "length(v.content) AS size, u.display_name AS owner_name "
                  "FROM videos v LEFT JOIN users u ON u.id=v.owner_id ")


@route("GET", r"/api/videos")
def api_videos(h, conn, user, body):
    return [dict(r) for r in conn.execute(VIDEO_LIST_SQL + "ORDER BY v.updated DESC")]


@route("POST", r"/api/videos")
def api_videos_create(h, conn, user, body):
    title = clean(body.get("title"), 200, "Title")
    if not title:
        raise HttpError(400, "Title required")
    now = time.time()
    cur = conn.execute(
        "INSERT INTO videos(title,status,owner_id,content,created,updated) VALUES(?,?,?,?,?,?)",
        (title, "idea", user["id"], get_setting(conn, "script_template", ""), now, now))
    return {"id": cur.lastrowid}


def get_video(conn, vid):
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not v:
        raise HttpError(404, "Video not found")
    return v


@route("GET", r"/api/videos/(\d+)")
def api_video(h, conn, user, body, vid):
    return dict(get_video(conn, vid))


@route("PATCH", r"/api/videos/(\d+)")
def api_video_patch(h, conn, user, body, vid):
    get_video(conn, vid)
    sets, vals = [], []
    if "title" in body:
        t = clean(body["title"], 200, "Title")
        if not t:
            raise HttpError(400, "Title required")
        sets.append("title=?"); vals.append(t)
    if "status" in body:
        if body["status"] not in STATUSES:
            raise HttpError(400, "Invalid status")
        sets.append("status=?"); vals.append(body["status"])
    if "owner_id" in body:
        oid = body["owner_id"]
        if oid is not None and not conn.execute("SELECT 1 FROM users WHERE id=?", (oid,)).fetchone():
            raise HttpError(400, "Unknown user")
        sets.append("owner_id=?"); vals.append(oid)
    if "deadline" in body:
        d = clean(body["deadline"], 10, "Deadline")
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise HttpError(400, "Deadline must be YYYY-MM-DD")
        sets.append("deadline=?"); vals.append(d)
    if "notes" in body:
        sets.append("notes=?"); vals.append(clean(body["notes"], 20000, "Notes"))
    if not sets:
        raise HttpError(400, "Nothing to update")
    sets.append("updated=?"); vals.append(time.time())
    conn.execute(f"UPDATE videos SET {','.join(sets)} WHERE id=?", vals + [vid])
    return {"ok": True}


@route("DELETE", r"/api/videos/(\d+)")
def api_video_delete(h, conn, user, body, vid):
    v = get_video(conn, vid)
    if not user["is_admin"] and v["owner_id"] != user["id"]:
        raise HttpError(403, "Only the owner or an admin can delete a video")
    conn.execute("DELETE FROM videos WHERE id=?", (vid,))
    return {"ok": True}


@route("PUT", r"/api/videos/(\d+)/script")
def api_script_save(h, conn, user, body, vid):
    v = get_video(conn, vid)
    content = body.get("content")
    if not isinstance(content, str) or len(content) > 1_000_000:
        raise HttpError(400, "Invalid content")
    if body.get("base_rev") != v["rev"]:
        # someone else saved since this editor loaded; never overwrite silently
        other = conn.execute("SELECT display_name FROM users u JOIN versions s ON s.user_id=u.id "
                             "WHERE s.video_id=? ORDER BY s.id DESC LIMIT 1", (vid,)).fetchone()
        return (409, {"error": "Script changed by someone else", "code": "conflict",
                      "author": other["display_name"] if other else None, "current_rev": v["rev"]})
    label = clean(body.get("label"), 100, "Label")
    if content != v["content"]:
        last = conn.execute("SELECT * FROM versions WHERE video_id=? ORDER BY id DESC LIMIT 1",
                            (vid,)).fetchone()
        now = time.time()
        if label or not last or last["user_id"] != user["id"] or now - last["created"] > VERSION_MIN_GAP:
            conn.execute("INSERT INTO versions(video_id,user_id,content,label,created) VALUES(?,?,?,?,?)",
                         (vid, user["id"], content, label, now))
        elif last:
            conn.execute("UPDATE versions SET content=?, created=? WHERE id=?", (content, now, last["id"]))
    elif label:
        conn.execute("INSERT INTO versions(video_id,user_id,content,label,created) VALUES(?,?,?,?,?)",
                     (vid, user["id"], content, label, time.time()))
    rev = v["rev"] + (1 if content != v["content"] else 0)
    conn.execute("UPDATE videos SET content=?, rev=?, updated=? WHERE id=?",
                 (content, rev, time.time(), vid))
    return {"rev": rev}


@route("GET", r"/api/videos/(\d+)/versions")
def api_versions(h, conn, user, body, vid):
    get_video(conn, vid)
    rows = conn.execute(
        "SELECT s.id,s.label,s.created,u.display_name AS author, length(s.content) AS size "
        "FROM versions s LEFT JOIN users u ON u.id=s.user_id WHERE s.video_id=? "
        "ORDER BY s.id DESC LIMIT 100", (vid,))
    return [dict(r) for r in rows]


@route("GET", r"/api/versions/(\d+)")
def api_version(h, conn, user, body, sid):
    r = conn.execute("SELECT * FROM versions WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HttpError(404, "Version not found")
    return dict(r)


@route("GET", r"/api/videos/(\d+)/comments")
def api_comments(h, conn, user, body, vid):
    get_video(conn, vid)
    rows = conn.execute(
        "SELECT c.id,c.body,c.created,c.user_id,u.display_name AS author FROM comments c "
        "LEFT JOIN users u ON u.id=c.user_id WHERE c.video_id=? ORDER BY c.id", (vid,))
    return [dict(r) for r in rows]


@route("POST", r"/api/videos/(\d+)/comments")
def api_comment_add(h, conn, user, body, vid):
    get_video(conn, vid)
    text = clean(body.get("body"), 5000, "Comment")
    if not text:
        raise HttpError(400, "Comment is empty")
    cur = conn.execute("INSERT INTO comments(video_id,user_id,body,created) VALUES(?,?,?,?)",
                       (vid, user["id"], text, time.time()))
    return {"id": cur.lastrowid}


@route("DELETE", r"/api/comments/(\d+)")
def api_comment_delete(h, conn, user, body, cid):
    c = conn.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
    if not c:
        raise HttpError(404, "Not found")
    if c["user_id"] != user["id"] and not user["is_admin"]:
        raise HttpError(403, "Not your comment")
    conn.execute("DELETE FROM comments WHERE id=?", (cid,))
    return {"ok": True}


# --- library: style guide, template, examples ---
@route("GET", r"/api/library")
def api_library(h, conn, user, body):
    return {
        "style_guide": get_setting(conn, "style_guide"),
        "script_template": get_setting(conn, "script_template"),
        "examples": [dict(r) for r in conn.execute(
            "SELECT id,title,length(body) AS size,created FROM examples ORDER BY id DESC")],
    }


@route("PUT", r"/api/library")
def api_library_save(h, conn, user, body):
    for key in ("style_guide", "script_template"):
        if key in body:
            if not isinstance(body[key], str) or len(body[key]) > 100_000:
                raise HttpError(400, f"Invalid {key}")
            set_setting(conn, key, body[key])
    return {"ok": True}


@route("GET", r"/api/examples/(\d+)")
def api_example(h, conn, user, body, eid):
    r = conn.execute("SELECT * FROM examples WHERE id=?", (eid,)).fetchone()
    if not r:
        raise HttpError(404, "Not found")
    return dict(r)


@route("POST", r"/api/examples")
def api_example_add(h, conn, user, body):
    title = clean(body.get("title"), 200, "Title")
    text = body.get("body")
    if not title or not isinstance(text, str) or not text.strip() or len(text) > 200_000:
        raise HttpError(400, "Title and script text are required")
    cur = conn.execute("INSERT INTO examples(title,body,created) VALUES(?,?,?)",
                       (title, text, time.time()))
    return {"id": cur.lastrowid}


@route("DELETE", r"/api/examples/(\d+)")
def api_example_delete(h, conn, user, body, eid):
    conn.execute("DELETE FROM examples WHERE id=?", (eid,))
    return {"ok": True}


# --- settings (admin) ---
def effective_key(conn, provider):
    return get_setting(conn, f"key_{provider}") or os.environ.get(ai.PROVIDERS[provider]["env"], "")


@route("GET", r"/api/settings")
def api_settings(h, conn, user, body):
    provider = get_setting(conn, "provider", "anthropic")
    return {
        "provider": provider,
        "model": get_setting(conn, "model"),
        "providers": {k: {"label": v["label"], "default_model": v["default_model"],
                          "has_key": bool(effective_key(conn, k))}
                      for k, v in ai.PROVIDERS.items()},
        "example_limit": int(get_setting(conn, "example_limit", "5")),
        "wpm": int(get_setting(conn, "wpm", "150")),
        "preset": get_preset(conn),
        "task_models": get_task_overrides(conn),
        "budget_usd": float(get_setting(conn, "budget_usd", "0") or 0),
        "rate_idr": float(get_setting(conn, "rate_idr", "0") or 0),
        "catalog": {"models": ai.CLAUDE_MODELS, "presets": ai.PRESETS, "tasks": ai.TASK_IDS,
                    "prices_checked": ai.PRICES_CHECKED, "prices_url": ai.PRICES_URL},
        "usage": month_usage(conn),
    }


@route("PUT", r"/api/settings")
def api_settings_save(h, conn, user, body):
    need_admin(user)
    if "provider" in body:
        if body["provider"] not in ai.PROVIDERS:
            raise HttpError(400, "Unknown provider")
        set_setting(conn, "provider", body["provider"])
    if "model" in body:
        set_setting(conn, "model", clean(body["model"], 100, "Model"))
    if "example_limit" in body:
        try:
            n = max(0, min(20, int(body["example_limit"])))
        except (TypeError, ValueError):
            raise HttpError(400, "Invalid example limit")
        set_setting(conn, "example_limit", str(n))
    if "wpm" in body:
        try:
            w = int(body["wpm"])
            if not 60 <= w <= 300:
                raise ValueError
        except (TypeError, ValueError):
            raise HttpError(400, "Invalid wpm")
        set_setting(conn, "wpm", str(w))
    if "preset" in body:
        if body["preset"] not in ai.PRESETS:
            raise HttpError(400, "Invalid preset")
        set_setting(conn, "preset", body["preset"])
    if "task_models" in body:
        tm = body["task_models"]
        if not isinstance(tm, dict) or any(k not in ai.TASK_IDS or (v and v not in ai.MODEL_BY_ID) for k, v in tm.items()):
            raise HttpError(400, "Unknown model")
        set_setting(conn, "task_models", json.dumps({k: v for k, v in tm.items() if v}))
    for key, label in (("budget_usd", "budget"), ("rate_idr", "rate")):
        if key in body:
            try:
                v = float(body[key] or 0)
                if not 0 <= v <= 1e9:
                    raise ValueError
            except (TypeError, ValueError):
                raise HttpError(400, f"Invalid {label}")
            set_setting(conn, key, str(v))
    keys = body.get("api_keys") or {}
    for p, k in keys.items():
        if p in ai.PROVIDERS and isinstance(k, str):
            set_setting(conn, f"key_{p}", k.strip())
    return {"ok": True}


# --- AI ---
@route("GET", r"/api/ai/stages")
def api_ai_stages(h, conn, user, body):
    return [{"id": k, "label": v["label"], "needs": v["needs"]} for k, v in ai.STAGES.items()]


def get_preset(conn):
    p = get_setting(conn, "preset", ai.DEFAULT_PRESET)
    return p if p in ai.PRESETS else ai.DEFAULT_PRESET


def get_task_overrides(conn):
    try:
        d = json.loads(get_setting(conn, "task_models", "{}") or "{}")
    except ValueError:
        d = {}
    return {k: v for k, v in d.items() if k in ai.TASK_IDS and v in ai.MODEL_BY_ID}


def model_for(conn, provider, stage_id, override=None):
    """override (this run) > custom model field > per-task setting > preset. Claude only for presets."""
    if override:
        if provider == "anthropic" and override not in ai.MODEL_BY_ID:
            raise HttpError(400, "Unknown model")
        return override
    custom = get_setting(conn, "model")
    if custom:
        return custom
    if provider != "anthropic":
        return ""
    return get_task_overrides(conn).get(stage_id) or ai.PRESETS[get_preset(conn)][stage_id]


def month_usage(conn):
    start = time.mktime(time.strptime(time.strftime("%Y-%m-01", time.localtime()), "%Y-%m-%d"))
    r = conn.execute("SELECT COALESCE(SUM(cost_usd),0) c, COUNT(*) n, "
                     "SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) unpriced "
                     "FROM ai_runs WHERE created>=?", (start,)).fetchone()
    return {"spent_usd": round(r["c"], 6), "runs": r["n"], "unpriced_runs": r["unpriced"] or 0,
            "month": time.strftime("%Y-%m")}


def check_budget(conn, body):
    budget = float(get_setting(conn, "budget_usd", "0") or 0)
    if budget > 0 and month_usage(conn)["spent_usd"] >= budget and not body.get("confirm_over"):
        return (409, {"error": "Monthly budget reached", "code": "over_budget",
                      "budget_usd": budget, "spent_usd": month_usage(conn)["spent_usd"]})
    return None


def prepare_ai(conn, body):
    stage = ai.STAGES.get(body.get("stage"))
    if not stage:
        raise HttpError(400, "Unknown stage")
    text_in = body.get("input") if isinstance(body.get("input"), str) else ""
    script = body.get("script") if isinstance(body.get("script"), str) else ""
    if stage["needs"] in ("input", "both") and not text_in.strip():
        raise HttpError(400, "Please type some input for this stage")
    if stage["needs"] in ("script", "both") and not script.strip():
        raise HttpError(400, "The script is empty")
    vid = body.get("video_id")
    if vid is not None:
        get_video(conn, vid)
    provider = get_setting(conn, "provider", "anthropic")
    limit = int(get_setting(conn, "example_limit", "5"))
    examples = [dict(r) for r in conn.execute(
        "SELECT title, body FROM examples ORDER BY id DESC LIMIT ?", (limit,))]
    return {"stage_id": body["stage"], "text_in": text_in, "vid": vid, "provider": provider,
            "system": ai.build_system(get_setting(conn, "style_guide"), examples),
            "user_msg": stage["prompt"].format(input=text_in, script=script),
            "key": effective_key(conn, provider)}


def log_run(user, p, res):
    """Store one finished run in its own connection (the request connection is released while the model thinks)."""
    u = res["usage"]
    cost = ai.cost_usd(res["model"], u) if p["provider"] == "anthropic" else None
    c = db()
    try:
        cur = c.execute(
            "INSERT INTO ai_runs(video_id,user_id,stage,input,output,created,model,in_tokens,out_tokens,"
            "cache_read,cache_write,cost_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["vid"], user["id"], p["stage_id"], p["text_in"][:20000], res["text"], time.time(), res["model"],
             u["input"], u["output"], u["cache_read"], u["cache_write"], cost))
        c.commit()
        rid = cur.lastrowid
        usage = month_usage(c)
    finally:
        c.close()
    return {"id": rid, "text": res["text"], "model": res["model"], "usage": u, "cost_usd": cost,
            "cached": u["cache_read"] > 0, "month": usage}


@route("POST", r"/api/ai")
def api_ai(h, conn, user, body):
    p = prepare_ai(conn, body)
    over = check_budget(conn, body)
    if over:
        return over
    model = model_for(conn, p["provider"], p["stage_id"], body.get("model"))
    conn.commit()
    res = ai.generate(p["provider"], model, p["key"], p["system"], p["user_msg"])
    return log_run(user, p, res)


@route("POST", r"/api/ai/compare")
def api_ai_compare(h, conn, user, body):
    p = prepare_ai(conn, body)
    if p["provider"] != "anthropic":
        raise HttpError(400, "Compare is only available with Claude")
    models = body.get("models")
    if (not isinstance(models, list) or len(models) != 2 or models[0] == models[1]
            or any(m not in ai.MODEL_BY_ID for m in models)):
        raise HttpError(400, "Choose two different models")
    over = check_budget(conn, body)
    if over:
        return over
    conn.commit()
    from concurrent.futures import ThreadPoolExecutor

    def one(m):
        try:
            return ai.generate(p["provider"], m, p["key"], p["system"], p["user_msg"])
        except ai.AIError as e:
            return {"error": str(e), "model": m}
    with ThreadPoolExecutor(2) as ex:
        outs = list(ex.map(one, models))
    results = [({"model": o["model"], "error": o["error"]} if "error" in o else log_run(user, p, o)) for o in outs]
    if all("error" in r for r in results):
        raise ai.AIError(results[0]["error"])
    return {"results": results}


@route("POST", r"/api/ai/estimate")
def api_ai_estimate(h, conn, user, body):
    stage_id = body.get("stage")
    stage = ai.STAGES.get(stage_id)
    if not stage:
        raise HttpError(400, "Unknown stage")
    try:
        in_chars = max(0, int(body.get("input_chars") or 0)); script_chars = max(0, int(body.get("script_chars") or 0))
    except (TypeError, ValueError):
        raise HttpError(400, "Invalid content")
    limit = int(get_setting(conn, "example_limit", "5"))
    ex = conn.execute("SELECT COALESCE(SUM(length(body)+length(title)),0) c FROM (SELECT title, body FROM examples "
                      "ORDER BY id DESC LIMIT ?)", (limit,)).fetchone()["c"]
    sys_chars = len(ai.BASE_RULES) + len(get_setting(conn, "style_guide")) + ex + 200
    user_chars = len(stage["prompt"]) + in_chars + (script_chars if stage["needs"] in ("script", "both") else 0)
    st, ut = ai.est_tokens(sys_chars), ai.est_tokens(user_chars)
    avg = conn.execute("SELECT AVG(out_tokens) a, COUNT(*) n FROM (SELECT out_tokens FROM ai_runs WHERE stage=? "
                       "AND out_tokens>0 ORDER BY id DESC LIMIT 10)", (stage_id,)).fetchone()
    out_t = int(avg["a"]) if avg["n"] and avg["n"] >= 2 else ai.TYPICAL_OUT_TOKENS[stage_id]
    now = time.time()
    rows = []
    for m in ai.CLAUDE_MODELS:
        cold, warm = ai.estimate_cost(m["id"], st, ut, out_t)
        recent = conn.execute("SELECT 1 FROM ai_runs WHERE model=? AND created>? AND (cache_read>0 OR cache_write>0) LIMIT 1",
                              (m["id"], now - 300)).fetchone()
        rows.append({"id": m["id"], "cold_usd": cold, "warm_usd": warm, "cache_likely": bool(recent),
                     "cacheable": st >= m["min_cache"]})
    provider = get_setting(conn, "provider", "anthropic")
    return {"models": rows, "default_model": model_for(conn, provider, stage_id),
            "system_tokens": st, "user_tokens": ut, "out_tokens": out_t,
            "out_from_history": bool(avg["n"] and avg["n"] >= 2), "usage": month_usage(conn)}


@route("GET", r"/api/videos/(\d+)/ai_runs")
def api_ai_runs(h, conn, user, body, vid):
    get_video(conn, vid)
    rows = conn.execute(
        "SELECT r.id,r.stage,r.input,r.output,r.created,r.model,r.in_tokens,r.out_tokens,r.cache_read,r.cache_write,r.cost_usd,"
        "u.display_name AS author FROM ai_runs r "
        "LEFT JOIN users u ON u.id=r.user_id WHERE r.video_id=? ORDER BY r.id DESC LIMIT 50", (vid,))
    return [dict(r) for r in rows]


def is_loopback(host):
    return host in ("127.0.0.1", "::1", "localhost")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("STUDIO_HOST", "127.0.0.1"),
                    help="address to listen on (env STUDIO_HOST). Use 0.0.0.0 to accept other machines")
    ap.add_argument("--port", type=int, default=int(os.environ.get("STUDIO_PORT", "8000")),
                    help="port (env STUDIO_PORT)")
    args = ap.parse_args()
    init_db()
    if not is_loopback(args.host):
        c = db()
        fresh = c.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
        c.close()
        if fresh and not os.environ.get("STUDIO_SETUP_TOKEN"):
            print("WARNING: no users yet and STUDIO_SETUP_TOKEN is not set. Anyone who reaches this server "
                  "first can create the admin account.", file=sys.stderr, flush=True)
        print("NOTE: this server speaks plain HTTP. Put an HTTPS reverse proxy in front of it "
              "if it is reachable from outside.", file=sys.stderr, flush=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Story Studio running on http://{args.host}:{args.port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye")


if __name__ == "__main__":
    main()
