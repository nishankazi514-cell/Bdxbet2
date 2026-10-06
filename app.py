from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, secrets, re
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
PHONE_REGEX = re.compile(r"^01[0-9]{9}$")

def get_db():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE NOT NULL,
        phone TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT NOT NULL,
        balance REAL DEFAULT 0,
        bonus REAL DEFAULT 0,
        total_deposit REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL,
        type TEXT NOT NULL,
        amount REAL NOT NULL,
        method TEXT,
        trx_id TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()
    db.close()

def normalize_phone(phone):
    phone = str(phone or "").strip().replace(" ", "").replace("-", "")
    if phone.startswith("+880"): phone = "0" + phone[4:]
    elif phone.startswith("880"): phone = "0" + phone[3:]
    elif phone.startswith("1") and len(phone) == 10: phone = "0" + phone
    return phone

def make_uid(db):
    while True:
        uid = "LK-" + secrets.token_hex(4).upper()
        if not db.execute("SELECT id FROM users WHERE uid=?", (uid,)).fetchone():
            return uid

def user_json(u):
    return {"uid":u["uid"],"phone":u["phone"],"name":u["name"],
            "balance":float(u["balance"] or 0),"bonus":float(u["bonus"] or 0)}

def required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return jsonify(success=False, message="Login required"), 401
        return fn(*a, **kw)
    return wrapper

@app.route("/")
def index():
    return send_from_directory(os.path.join(os.path.dirname(__file__),"templates"), "index.html")

@app.post("/api/register")
def register():
    d=request.get_json(silent=True) or {}
    name=str(d.get("name","")).strip()
    phone=normalize_phone(d.get("phone"))
    password=str(d.get("password",""))
    if len(name)<3: return jsonify(success=False,message="নাম কমপক্ষে ৩ অক্ষরের হতে হবে"),400
    if not PHONE_REGEX.match(phone): return jsonify(success=False,message="সঠিক মোবাইল নম্বর দিন"),400
    if len(password)<6: return jsonify(success=False,message="পাসওয়ার্ড কমপক্ষে ৬ অক্ষরের হতে হবে"),400
    db=get_db()
    if db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone():
        db.close(); return jsonify(success=False,message="এই নম্বর দিয়ে আগে থেকেই account আছে"),409
    uid=make_uid(db)
    db.execute("INSERT INTO users(uid,phone,password,name,balance,bonus) VALUES(?,?,?,?,0,100)",
               (uid,phone,generate_password_hash(password),name))
    db.commit()
    u=db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    db.close()
    session["user_id"]=u["id"]
    return jsonify(success=True,message="Registration successful",user=user_json(u))

@app.post("/api/login")
def login():
    d=request.get_json(silent=True) or {}
    phone=normalize_phone(d.get("phone"))
    password=str(d.get("password",""))
    db=get_db(); u=db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone(); db.close()
    if not u: return jsonify(success=False,message="Account পাওয়া যায়নি"),401
    if not check_password_hash(u["password"],password):
        return jsonify(success=False,message="পাসওয়ার্ড ভুল"),401
    session["user_id"]=u["id"]
    return jsonify(success=True,message="Login successful",user=user_json(u))

@app.get("/api/me")
@required
def me():
    db=get_db(); u=db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone(); db.close()
    if not u: session.clear(); return jsonify(success=False,message="User not found"),404
    return jsonify(success=True,user=user_json(u))

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(success=True,message="Logged out")

@app.get("/api/balance")
@required
def balance():
    db=get_db(); u=db.execute("SELECT balance,bonus FROM users WHERE id=?", (session["user_id"],)).fetchone(); db.close()
    if not u: return jsonify(success=False),404
    return jsonify(success=True,balance=float(u["balance"] or 0),bonus=float(u["bonus"] or 0))

@app.post("/api/deposit")
@required
def deposit():
    d=request.get_json(silent=True) or {}
    method=str(d.get("method","")).strip(); trx=str(d.get("trxId","")).strip()
    try: amount=float(d.get("amount",0))
    except: amount=0
    if amount<500: return jsonify(success=False,message="সর্বনিম্ন ৫০০ টাকা"),400
    if not trx: return jsonify(success=False,message="Transaction ID দিন"),400
    db=get_db(); u=db.execute("SELECT uid FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not u: db.close(); return jsonify(success=False,message="User not found"),404
    db.execute("INSERT INTO transactions(uid,type,amount,method,trx_id) VALUES(?,'deposit',?,?,?)",
               (u["uid"],amount,method,trx))
    db.commit(); db.close()
    return jsonify(success=True,message="ডিপোজিট রিকোয়েস্ট সাবমিট হয়েছে!")

@app.post("/api/withdraw")
@required
def withdraw():
    d=request.get_json(silent=True) or {}
    method=str(d.get("method","")).strip(); phone=normalize_phone(d.get("phone"))
    try: amount=float(d.get("amount",0))
    except: amount=0
    if amount<1000: return jsonify(success=False,message="সর্বনিম্ন ১০০০ টাকা"),400
    if not PHONE_REGEX.match(phone): return jsonify(success=False,message="সঠিক মোবাইল নম্বর দিন"),400
    db=get_db(); u=db.execute("SELECT uid,balance FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not u: db.close(); return jsonify(success=False,message="User not found"),404
    if float(u["balance"])<amount:
        db.close(); return jsonify(success=False,message="পর্যাপ্ত balance নেই"),400
    db.execute("INSERT INTO transactions(uid,type,amount,method,trx_id) VALUES(?,'withdraw',?,?,?)",
               (u["uid"],amount,method,phone))
    db.commit(); db.close()
    return jsonify(success=True,message="উইথড্র রিকোয়েস্ট পাঠানো হয়েছে!")

@app.get("/api/user/<uid>")
@required
def find_user(uid):
    db=get_db(); u=db.execute("SELECT uid,name FROM users WHERE uid=?", (uid.strip().upper(),)).fetchone(); db.close()
    if not u: return jsonify(success=False,message="User পাওয়া যায়নি"),404
    return jsonify(success=True,user={"uid":u["uid"],"name":u["name"]})

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
