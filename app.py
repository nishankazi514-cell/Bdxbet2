from flask import Flask, request, jsonify, session, send_from_directory, abort, Response
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import secrets
import re
import tempfile
from html import escape
from functools import wraps

# =========================
# BDXbet DEMO - virtual coins only (no real money)
# =========================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")


def get_secret_key():
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    try:
        with open(SECRET_FILE, "w") as f:
            f.write(key)
    except Exception:
        pass
    return key


app.secret_key = get_secret_key()

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 days

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(TEMPLATES_DIR, exist_ok=True)


def pick_db_path():
    env = os.environ.get("DB_PATH")
    if env:
        return env
    path = os.path.join(BASE_DIR, "demo.db")
    try:
        with open(path, "a"):
            pass
        return path
    except OSError:
        return os.path.join(tempfile.gettempdir(), "demo.db")


DB_FILE = pick_db_path()

USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{3,20}$")

START_COINS = 100      # নতুন ইউজার পায়
REFILL_TO = 1000       # ফ্রি কয়েন নিলে এই পর্যন্ত ভরে দেওয়া হয়


# =========================
# DATABASE
# =========================

def get_db():
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                coins INTEGER DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    finally:
        db.close()


# =========================
# HELPERS
# =========================

def make_uid(db):
    for _ in range(50):
        uid = "DM-" + secrets.token_hex(4).upper()
        if not db.execute("SELECT id FROM users WHERE uid=?", (uid,)).fetchone():
            return uid
    raise Exception("UID generation failed")


def user_json(u):
    return {
        "uid": u["uid"],
        "username": u["username"],
        "phone": u["username"],   # পুরনো page এর সাথে মিল রাখার জন্য (আসলে username)
        "name": u["username"],
        "coins": int(u["coins"] or 0),
        "balance": int(u["coins"] or 0),
        "bonus": 0,
    }


def required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(success=False, message="আগে লগইন করুন"), 401
        return fn(*args, **kwargs)
    return wrapper


# =========================
# WEBSITE (pages)
# =========================

SEARCH_DIRS = (TEMPLATES_DIR, BASE_DIR, STATIC_DIR)

ALLOWED_EXT = {
    ".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", ".ico", ".mp3", ".wav", ".ogg", ".woff", ".woff2", ".ttf",
}


def find_file(filename):
    filename = str(filename).replace("\\", "/").lstrip("/")
    if not filename:
        return None, None

    for folder in SEARCH_DIRS:
        if os.path.isfile(os.path.join(folder, filename)):
            return folder, filename

    if "/" not in filename:
        low = filename.lower()
        for folder in SEARCH_DIRS:
            if not os.path.isdir(folder):
                continue
            for f in os.listdir(folder):
                if f.lower() == low and os.path.isfile(os.path.join(folder, f)):
                    return folder, f

    return None, None


def list_html_files():
    found = []
    for folder in SEARCH_DIRS:
        if os.path.isdir(folder):
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith(".html") and f not in found:
                    found.append(f)
    return found


def serve_page(filename):
    folder, name = find_file(filename)
    if folder:
        return send_from_directory(folder, name)

    links = "".join(
        f'<li><a href="/{escape(f)}">{escape(f)}</a></li>' for f in list_html_files()
    ) or "<li>কোনো .html file পাওয়া যায়নি</li>"
    page = (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<body style='font-family:sans-serif;padding:20px'>"
        f"<h2>{escape(filename)} পাওয়া যায়নি</h2>"
        "<p>Server চলছে, কিন্তু এই file টা repo তে নেই। "
        "<b>templates/</b> folder এ upload করুন।</p>"
        f"<p>এখন যেসব html file আছে:</p><ul>{links}</ul></body>"
    )
    return Response(page, status=404, mimetype="text/html")


@app.route("/")
@app.route("/index.html")
def index():
    return serve_page("index.html")


@app.route("/shuvoludo.html")
def ludo():
    return serve_page("shuvoludo.html")


@app.route("/healthz")
def healthz():
    return jsonify(ok=True)


@app.route("/<path:filename>")
def other_files(filename):
    if filename.startswith("api/"):
        return jsonify(success=False, message="Not found"), 404

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        abort(404)

    folder, name = find_file(filename)
    if not folder:
        abort(404)
    return send_from_directory(folder, name)


# =========================
# REGISTER
# =========================

@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}

    username = str(d.get("username", "")).strip()
    password = str(d.get("password", ""))

    if not USERNAME_REGEX.match(username):
        return jsonify(success=False, message="ইউজারনেম ৩-২০ অক্ষর, শুধু ইংরেজি অক্ষর, সংখ্যা বা _"), 400

    if len(password) < 6:
        return jsonify(success=False, message="পাসওয়ার্ড কমপক্ষে ৬ অক্ষরের হতে হবে"), 400

    db = get_db()
    try:
        exists = db.execute(
            "SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()
        if exists:
            return jsonify(success=False, message="এই ইউজারনেম আগে থেকেই আছে"), 409

        uid = make_uid(db)
        db.execute(
            "INSERT INTO users(uid, username, password, coins) VALUES(?,?,?,?)",
            (uid, username, generate_password_hash(password), START_COINS),
        )
        db.commit()

        u = db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        session["user_id"] = u["id"]
        session.permanent = True

        return jsonify(success=True, message="অ্যাকাউন্ট তৈরি হয়েছে", user=user_json(u))
    finally:
        db.close()


# =========================
# LOGIN / ME / LOGOUT
# =========================

@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    username = str(d.get("username", d.get("phone", ""))).strip()
    password = str(d.get("password", ""))

    db = get_db()
    try:
        u = db.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()

        if not u:
            return jsonify(success=False, message="অ্যাকাউন্ট পাওয়া যায়নি"), 401

        if not check_password_hash(u["password"], password):
            return jsonify(success=False, message="পাসওয়ার্ড ভুল"), 401

        session["user_id"] = u["id"]
        session.permanent = True
        return jsonify(success=True, message="লগইন হয়েছে", user=user_json(u))
    finally:
        db.close()


@app.get("/api/me")
@required
def me():
    db = get_db()
    try:
        u = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u:
            session.clear()
            return jsonify(success=False, message="User not found"), 404
        return jsonify(success=True, user=user_json(u))
    finally:
        db.close()


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(success=True, message="লগ আউট হয়েছে")


@app.get("/api/balance")
@required
def balance():
    db = get_db()
    try:
        u = db.execute("SELECT coins FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u:
            return jsonify(success=False), 404
        return jsonify(success=True, coins=int(u["coins"]), balance=int(u["coins"]), bonus=0)
    finally:
        db.close()


# =========================
# DEMO COINS (virtual, no real money)
# =========================

@app.post("/api/demo/claim")
@required
def demo_claim():
    db = get_db()
    try:
        u = db.execute("SELECT coins FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u:
            return jsonify(success=False, message="User not found"), 404

        if int(u["coins"]) >= REFILL_TO:
            return jsonify(success=False, message=f"আপনার কাছে আগেই {REFILL_TO} বা বেশি কয়েন আছে"), 400

        db.execute("UPDATE users SET coins=? WHERE id=?", (REFILL_TO, session["user_id"]))
        db.commit()
        return jsonify(success=True, message=f"ডেমো কয়েন {REFILL_TO} করে দেওয়া হয়েছে", coins=REFILL_TO)
    finally:
        db.close()


@app.post("/api/demo/reset")
@required
def demo_reset():
    db = get_db()
    try:
        db.execute("UPDATE users SET coins=? WHERE id=?", (START_COINS, session["user_id"]))
        db.commit()
        return jsonify(success=True, message=f"কয়েন রিসেট হয়েছে ({START_COINS})", coins=START_COINS)
    finally:
        db.close()


@app.get("/api/user/<uid>")
@required
def find_user(uid):
    db = get_db()
    try:
        u = db.execute(
            "SELECT uid, username FROM users WHERE uid=?", (uid.strip().upper(),)
        ).fetchone()
        if not u:
            return jsonify(success=False, message="User পাওয়া যায়নি"), 404
        return jsonify(success=True, user={"uid": u["uid"], "name": u["username"]})
    finally:
        db.close()


# =========================
# INIT / RUN
# =========================

try:
    init_db()
except Exception as e:
    print("init_db error:", e)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
    )
