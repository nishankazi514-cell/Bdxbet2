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
# APP CONFIG
# =========================

app = Flask(__name__)

# FIX 1: Persistent secret key
# ENV variable না থাকলে file-এ save করে রাখে, restart-এ same থাকে
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
    except:
        pass
    return key

app.secret_key = get_secret_key()

# FIX 2: Session security settings
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 days

# FIX 3: Ensure templates dir exists
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)

STATIC_DIR = os.path.join(BASE_DIR, "static")


def pick_db_path():
    # DB_PATH env > app.py এর পাশে users.db > (read-only হলে) temp folder
    env = os.environ.get("DB_PATH")
    if env:
        return env
    path = os.path.join(BASE_DIR, "users.db")
    try:
        with open(path, "a"):
            pass
        return path
    except OSError:
        return os.path.join(tempfile.gettempdir(), "users.db")


DB_FILE = pick_db_path()

PHONE_REGEX = re.compile(r"^01[0-9]{9}$")


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
                phone TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                balance REAL DEFAULT 0,
                bonus REAL DEFAULT 0,
                total_deposit REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                method TEXT,
                trx_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.commit()
    finally:
        db.close()


# =========================
# HELPERS
# =========================

def normalize_phone(phone):
    phone = str(phone or "").strip()
    phone = phone.replace(" ", "").replace("-", "")

    if phone.startswith("+880"):
        phone = "0" + phone[4:]
    elif phone.startswith("880"):
        phone = "0" + phone[3:]
    elif phone.startswith("1") and len(phone) == 10:
        phone = "0" + phone

    return phone


def make_uid(db):
    for _ in range(50):
        uid = "LK-" + secrets.token_hex(4).upper()
        exists = db.execute(
            "SELECT id FROM users WHERE uid=?",
            (uid,)
        ).fetchone()
        if not exists:
            return uid
    raise Exception("UID generation failed")


def user_json(u):
    return {
        "uid": u["uid"],
        "phone": u["phone"],
        "name": u["name"],
        "balance": float(u["balance"] or 0),
        "bonus": float(u["bonus"] or 0)
    }


def required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(success=False, message="Login required"), 401
        return fn(*args, **kwargs)
    return wrapper


# =========================
# MAIN WEBSITE
# =========================

SEARCH_DIRS = (TEMPLATES_DIR, BASE_DIR, STATIC_DIR)

# শুধু এই ধরনের file browser কে দেওয়া হবে (app.py, users.db, .secret_key কখনো না)
ALLOWED_EXT = {
    ".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", ".ico", ".mp3", ".wav", ".ogg", ".woff", ".woff2", ".ttf",
}


def find_file(filename):
    """templates, app.py এর পাশের folder, static - সব জায়গায় খোঁজে (case-insensitive সহ)"""
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

    # index.html না থাকলে অন্য কোনো html file থাকলে সেটাই দেখাবে
    if filename == "index.html":
        others = [f for f in list_html_files() if f.lower() != "shuvoludo.html"]
        if others:
            folder, name = find_file(others[0])
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


# অন্য সব page / css / js / image (যেমন /style.css)
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

    name = str(d.get("name", "")).strip()
    phone = normalize_phone(d.get("phone"))
    password = str(d.get("password", ""))

    if len(name) < 3:
        return jsonify(success=False, message="নাম কমপক্ষে ৩ অক্ষরের হতে হবে"), 400

    if not PHONE_REGEX.match(phone):
        return jsonify(success=False, message="সঠিক মোবাইল নম্বর দিন"), 400

    if len(password) < 6:
        return jsonify(success=False, message="পাসওয়ার্ড কমপক্ষে ৬ অক্ষরের হতে হবে"), 400

    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM users WHERE phone=?",
            (phone,)
        ).fetchone()

        if existing:
            return jsonify(success=False, message="এই নম্বর দিয়ে আগে থেকেই account আছে"), 409

        uid = make_uid(db)

        db.execute("""
            INSERT INTO users(uid, phone, password, name, balance, bonus)
            VALUES(?,?,?,?,0,100)
        """, (
            uid,
            phone,
            generate_password_hash(password),
            name
        ))

        db.commit()

        u = db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()

        session["user_id"] = u["id"]
        session.permanent = True

        return jsonify(
            success=True,
            message="Registration successful",
            user=user_json(u)
        )
    finally:
        db.close()


# =========================
# LOGIN
# =========================

@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}

    phone = normalize_phone(d.get("phone"))
    password = str(d.get("password", ""))

    db = get_db()
    try:
        u = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()

        if not u:
            return jsonify(success=False, message="Account পাওয়া যায়নি"), 401

        if not check_password_hash(u["password"], password):
            return jsonify(success=False, message="পাসওয়ার্ড ভুল"), 401

        session["user_id"] = u["id"]
        session.permanent = True

        return jsonify(
            success=True,
            message="Login successful",
            user=user_json(u)
        )
    finally:
        db.close()


# =========================
# CURRENT USER
# =========================

@app.get("/api/me")
@required
def me():
    db = get_db()
    try:
        u = db.execute(
            "SELECT * FROM users WHERE id=?",
            (session["user_id"],)
        ).fetchone()

        if not u:
            session.clear()
            return jsonify(success=False, message="User not found"), 404

        return jsonify(success=True, user=user_json(u))
    finally:
        db.close()


# =========================
# LOGOUT
# =========================

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(success=True, message="Logged out")


# =========================
# BALANCE
# =========================

@app.get("/api/balance")
@required
def balance():
    db = get_db()
    try:
        u = db.execute(
            "SELECT balance,bonus FROM users WHERE id=?",
            (session["user_id"],)
        ).fetchone()

        if not u:
            return jsonify(success=False), 404

        return jsonify(
            success=True,
            balance=float(u["balance"] or 0),
            bonus=float(u["bonus"] or 0)
        )
    finally:
        db.close()


# =========================
# DEPOSIT
# =========================

@app.post("/api/deposit")
@required
def deposit():
    d = request.get_json(silent=True) or {}

    method = str(d.get("method", "")).strip()
    trx = str(d.get("trxId", "")).strip()

    try:
        amount = float(d.get("amount", 0))
    except:
        amount = 0

    if amount < 500:
        return jsonify(success=False, message="সর্বনিম্ন ৫০০ টাকা"), 400

    if not trx:
        return jsonify(success=False, message="Transaction ID দিন"), 400

    db = get_db()
    try:
        u = db.execute(
            "SELECT uid FROM users WHERE id=?",
            (session["user_id"],)
        ).fetchone()

        if not u:
            return jsonify(success=False, message="User not found"), 404

        db.execute("""
            INSERT INTO transactions(uid, type, amount, method, trx_id)
            VALUES(?,'deposit',?,?,?)
        """, (u["uid"], amount, method, trx))

        db.commit()

        return jsonify(
            success=True,
            message="ডিপোজিট রিকোয়েস্ট সাবমিট হয়েছে!"
        )
    finally:
        db.close()


# =========================
# WITHDRAW
# =========================

@app.post("/api/withdraw")
@required
def withdraw():
    d = request.get_json(silent=True) or {}

    method = str(d.get("method", "")).strip()
    phone = normalize_phone(d.get("phone"))

    try:
        amount = float(d.get("amount", 0))
    except:
        amount = 0

    if amount < 1000:
        return jsonify(success=False, message="সর্বনিম্ন ১০০০ টাকা"), 400

    if not PHONE_REGEX.match(phone):
        return jsonify(success=False, message="সঠিক মোবাইল নম্বর দিন"), 400

    db = get_db()
    try:
        u = db.execute("""
            SELECT uid,balance FROM users WHERE id=?
        """, (session["user_id"],)).fetchone()

        if not u:
            return jsonify(success=False, message="User not found"), 404

        if float(u["balance"]) < amount:
            return jsonify(success=False, message="পর্যাপ্ত balance নেই"), 400

        db.execute("""
            INSERT INTO transactions(uid, type, amount, method, trx_id)
            VALUES(?,'withdraw',?,?,?)
        """, (u["uid"], amount, method, phone))

        db.commit()

        return jsonify(
            success=True,
            message="উইথড্র রিকোয়েস্ট পাঠানো হয়েছে!"
        )
    finally:
        db.close()


# =========================
# FIND USER BY UID
# =========================

@app.get("/api/user/<uid>")
@required
def find_user(uid):
    db = get_db()
    try:
        u = db.execute("""
            SELECT uid,name FROM users WHERE uid=?
        """, (uid.strip().upper(),)).fetchone()

        if not u:
            return jsonify(success=False, message="User পাওয়া যায়নি"), 404

        return jsonify(
            success=True,
            user={"uid": u["uid"], "name": u["name"]}
        )
    finally:
        db.close()


# =========================
# DATABASE INITIALIZE
# =========================

try:
    init_db()
except Exception as e:
    print("init_db error:", e)


# =========================
# LOCAL RUN
# =========================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
        )
