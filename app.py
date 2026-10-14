"""
AI Investment Advisor Dashboard - Flask backend.
Run:  python app.py   then open http://127.0.0.1:5000
Educational prototype only. Not financial advice. All market data is simulated sample data.
"""
import csv
import io
import json
import math
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import pandas as pd

import data_loader as dl
import database as db
import engine

app = Flask(__name__)
db.init_db()
dl.ensure_sample_data()


def _secret():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(db.INSTANCE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    return open(path).read().strip()


app.config.update(SECRET_KEY=_secret(), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  MAX_CONTENT_LENGTH=2 * 1024 * 1024)
app.teardown_appcontext(db.close_db)

CURRENCIES = {"INR": "₹", "USD": "$", "EUR": "€"}
ALERT_KINDS = {"value_below": "Portfolio value below", "value_above": "Portfolio value above",
               "weight_above": "Category weight above (%)", "price_below": "Asset price below", "price_above": "Asset price above"}

# ------------------------------------------------------------------ helpers / security


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            if request.path.startswith("/api/"):
                return jsonify(error="Security token missing or expired. Refresh the page and try again."), 400
            abort(400)


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        g.user = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    return g.user


def login_required(view):
    @wraps(view)
    def wrapper(*a, **k):
        if not current_user():
            session.pop("uid", None)
            if request.path.startswith("/api/"):
                return jsonify(error="Please sign in first."), 401
            return redirect(url_for("login"))
        return view(*a, **k)
    return wrapper


def err(msg, code=400, **extra):
    return jsonify(error=msg, **extra), code


def num(v, allow_blank=False):
    if v is None or isinstance(v, bool):
        return None
    s = str(v).replace(",", "").strip()
    if s == "":
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def get_profile(uid):
    p = db.query("SELECT * FROM profiles WHERE user_id=?", (uid,), one=True)
    if not p:
        db.execute("INSERT INTO profiles(user_id) VALUES (?)", (uid,))
        p = db.query("SELECT * FROM profiles WHERE user_id=?", (uid,), one=True)
    try:
        p["answers"] = json.loads(p["answers"]) if p["answers"] else {}
    except ValueError:
        p["answers"] = {}
    return p


def load_state(uid):
    prof = get_profile(uid)
    rows = db.query("SELECT * FROM holdings WHERE user_id=? ORDER BY id", (uid,))
    enriched = engine.enrich_holdings(rows, dl.latest_prices())
    summary = engine.summarize(enriched)
    alloc = engine.allocation(enriched, summary)
    risk, _ = engine.compute_risk(prof)
    return dict(profile=prof, enriched=enriched, summary=summary, alloc=alloc, risk=risk)


def money(v, cur="INR"):
    if v is None:
        return "-"
    return f"{'-' if v < 0 else ''}{CURRENCIES.get(cur, '')}{abs(v):,.2f}"


@app.template_filter("money")
def money_filter(v, cur="INR"):
    return money(v, cur)


@app.template_filter("pct")
def pct_filter(v):
    return "-" if v is None else f"{v:+.2f}%"


@app.context_processor
def inject():
    user = current_user()
    prof = get_profile(user["id"]) if user else None
    return dict(user=user, prof=prof, csrf_token=csrf_token, data_label=dl.DATA_LABEL,
                currency=(prof or {}).get("currency", "INR"))

# ------------------------------------------------------------------ holdings validation


def validate_holding(d):
    errs, clean = {}, {}
    name = str(d.get("name") or "").strip()
    if not name:
        errs["name"] = "Asset name is required."
    elif len(name) > 80:
        errs["name"] = "Asset name must be 80 characters or fewer."
    clean["name"] = name
    cat = str(d.get("category") or "").strip()
    match = next((c for c in engine.CATEGORIES if c.lower() == cat.lower()), None)
    if not match:
        errs["category"] = "Choose a category: " + ", ".join(engine.CATEGORIES) + "."
    clean["category"] = match
    q = num(d.get("quantity"))
    if q is None or q <= 0:
        errs["quantity"] = "Quantity must be a number greater than 0."
    clean["quantity"] = q
    b = num(d.get("buy_price"))
    if b is None or b <= 0:
        errs["buy_price"] = "Purchase price must be a number greater than 0."
    clean["buy_price"] = b
    raw_latest = d.get("latest_price")
    if raw_latest is None or str(raw_latest).strip() == "":
        clean["latest_price"] = None
    else:
        lp = num(raw_latest)
        if lp is None or lp < 0:
            errs["latest_price"] = "Latest price must be a number of 0 or more (or leave it blank)."
        clean["latest_price"] = lp
    sym = str(d.get("symbol") or "").strip().upper()
    if sym and (sym not in dl.CATALOG or sym == dl.BENCHMARK):
        errs["symbol"] = "Unknown sample symbol. Leave blank for a manually priced asset."
    clean["symbol"] = sym or None
    return clean, errs


def public_holding(h):
    keys = ("id", "name", "symbol", "category", "quantity", "buy_price", "latest_price", "price", "price_source",
            "cost", "value", "pl", "ret", "weight")
    return {k: h.get(k) for k in keys}

# ------------------------------------------------------------------ demo / seeding


def seed_persona(uid, key):
    p = dl.PERSONAS[key]
    for t in ("holdings", "scenarios", "alerts", "watchlist"):
        db.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    for h in p["holdings"]:
        if "symbol" in h:
            meta = dl.CATALOG[h["symbol"]]
            buy = dl.price_on_or_before(h["symbol"], h["days_ago"])
            db.execute("INSERT INTO holdings(user_id,name,symbol,category,quantity,buy_price,latest_price) VALUES (?,?,?,?,?,?,NULL)",
                       (uid, meta["name"], h["symbol"], meta["category"], round(h["amount"] / buy, 2), buy))
        else:
            db.execute("INSERT INTO holdings(user_id,name,symbol,category,quantity,buy_price,latest_price) VALUES (?,?,NULL,?,?,?,?)",
                       (uid, h["name"], h["category"], 1, h["manual_cost"], h["manual_value"]))
    for sym in ("EQ-LARGE", "GOLD-ETF", "MF-INDEX"):
        db.execute("INSERT OR IGNORE INTO watchlist(user_id,symbol) VALUES (?,?)", (uid, sym))
    prof = dict(display_name=p["display_name"], experience=p["experience"], goal=p["goal"],
                horizon_years=p["horizon_years"], preferred_risk=p["preferred_risk"], answers=p["answers"])
    save_profile(uid, prof)


def save_profile(uid, prof):
    risk, _ = engine.compute_risk(prof)
    get_profile(uid)
    db.execute("""UPDATE profiles SET display_name=?, experience=?, goal=?, horizon_years=?, preferred_risk=?, answers=?,
                  risk_score=?, risk_category=?, updated_at=? WHERE user_id=?""",
               (prof.get("display_name"), prof.get("experience"), prof.get("goal"), prof.get("horizon_years"),
                prof.get("preferred_risk"), json.dumps(prof.get("answers") or {}),
                risk["score"] if risk else None, risk["category"] if risk else None,
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), uid))
    return risk

# ------------------------------------------------------------------ auth + pages


@app.get("/")
def index():
    return redirect(url_for("dashboard" if current_user() else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = db.query("SELECT * FROM users WHERE username=? AND is_demo=0", (request.form.get("username", "").strip().lower(),), one=True)
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = u["id"]
            return redirect(url_for("dashboard"))
        error = "Incorrect username or password."
    return render_template("login.html", error=error, personas=dl.PERSONAS, mode="login")


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if not re.fullmatch(r"[a-z0-9_]{3,30}", username):
            error = "Username must be 3-30 characters: letters, numbers or underscore."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif pw != pw2:
            error = "Passwords do not match."
        elif db.query("SELECT 1 FROM users WHERE username=?", (username,), one=True):
            error = "That username is already taken."
        else:
            uid = db.execute("INSERT INTO users(username,password_hash) VALUES (?,?)", (username, generate_password_hash(pw)))
            get_profile(uid)
            session.clear()
            session["uid"] = uid
            return redirect(url_for("portfolio"))
    return render_template("login.html", error=error, personas=dl.PERSONAS, mode="register")


@app.get("/demo")
def demo():
    key = request.args.get("profile", "moderate")
    if key not in dl.PERSONAS:
        key = "moderate"
    db.execute("DELETE FROM users WHERE is_demo=1 AND created_at < datetime('now','-1 day')")
    uid = db.execute("INSERT INTO users(username,is_demo,persona) VALUES (?,1,?)", ("demo_" + secrets.token_hex(5), key))
    seed_persona(uid, key)
    session.clear()
    session["uid"] = uid
    return redirect(url_for("dashboard"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def page(template, active):
    @login_required
    def view():
        return render_template(template, active=active)
    view.__name__ = "page_" + active
    return view


for _path, _tpl, _name in [("/dashboard", "dashboard.html", "dashboard"), ("/portfolio", "portfolio.html", "portfolio"),
                           ("/risk", "risk.html", "risk"), ("/insights", "insights.html", "insights"),
                           ("/compare", "compare.html", "compare"), ("/goal", "goal.html", "goal"),
                           ("/watchlist", "watchlist.html", "watchlist"), ("/settings", "settings.html", "settings")]:
    app.add_url_rule(_path, endpoint=_name, view_func=page(_tpl, _name))


@app.get("/report")
@login_required
def report():
    st = load_state(current_user()["id"])
    p = st["profile"]
    status = dl.data_status()
    insights = engine.generate_insights(st["enriched"], st["summary"], st["alloc"], st["risk"], p, p["conc_threshold"], status)
    perf = engine.performance(st["enriched"], dl.load_prices(), p["default_range"], "M")
    return render_template("report.html", active="report", st=st, insights=insights, perf=perf, status=status,
                           today=datetime.now().strftime("%d %b %Y, %H:%M"), cur=p["currency"])

# ------------------------------------------------------------------ API: summary / holdings


@app.get("/api/summary")
@login_required
def api_summary():
    uid = current_user()["id"]
    st = load_state(uid)
    p, status = st["profile"], dl.data_status()
    rng = request.args.get("range", p["default_range"])
    freq = request.args.get("freq", "D")
    if rng not in engine.RANGES or freq not in engine.FREQS:
        return err("Invalid range or frequency.")
    insights = engine.generate_insights(st["enriched"], st["summary"], st["alloc"], st["risk"], p, p["conc_threshold"], status)
    sources = sorted({h["price_source"] for h in st["enriched"] if h["value"] is not None})
    kind = "Sample portfolio (demo)" if current_user()["is_demo"] else "Manually entered holdings"
    scen = db.query("SELECT id,name,kind,created_at FROM scenarios WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,))
    return jsonify(summary=st["summary"], alloc=st["alloc"], insights=insights[:4], status=status, scenarios=scen,
                   performance=engine.performance(st["enriched"], dl.load_prices(), rng, freq),
                   risk=st["risk"] and dict(category=st["risk"]["category"], score=st["risk"]["score"]),
                   meta=dict(portfolio_source=kind, price_sources=sources, currency=p["currency"], range=rng, freq=freq,
                             alerts=len([a for a in evaluate_alerts(uid) if a["triggered"]])))


@app.get("/api/holdings")
@login_required
def api_holdings():
    st = load_state(current_user()["id"])
    return jsonify(holdings=[public_holding(h) for h in st["enriched"]], summary=st["summary"], alloc=st["alloc"],
                   threshold=st["profile"]["conc_threshold"], categories=engine.CATEGORIES,
                   catalog={k: dict(name=v["name"], category=v["category"]) for k, v in dl.CATALOG.items() if k != dl.BENCHMARK},
                   is_demo=bool(current_user()["is_demo"]))


@app.post("/api/holdings")
@login_required
def api_add_holding():
    uid = current_user()["id"]
    if db.query("SELECT COUNT(*) c FROM holdings WHERE user_id=?", (uid,), one=True)["c"] >= 200:
        return err("Holding limit (200) reached for this prototype.")
    clean, errs = validate_holding(request.get_json(silent=True) or {})
    if errs:
        return err("Please fix the highlighted fields.", fields=errs)
    hid = db.execute("INSERT INTO holdings(user_id,name,symbol,category,quantity,buy_price,latest_price) VALUES (?,?,?,?,?,?,?)",
                     (uid, clean["name"], clean["symbol"], clean["category"], clean["quantity"], clean["buy_price"], clean["latest_price"]))
    return jsonify(id=hid), 201


@app.put("/api/holdings/<int:hid>")
@login_required
def api_edit_holding(hid):
    uid = current_user()["id"]
    if not db.query("SELECT 1 FROM holdings WHERE id=? AND user_id=?", (hid, uid), one=True):
        return err("Holding not found.", 404)
    clean, errs = validate_holding(request.get_json(silent=True) or {})
    if errs:
        return err("Please fix the highlighted fields.", fields=errs)
    db.execute("UPDATE holdings SET name=?,symbol=?,category=?,quantity=?,buy_price=?,latest_price=? WHERE id=? AND user_id=?",
               (clean["name"], clean["symbol"], clean["category"], clean["quantity"], clean["buy_price"], clean["latest_price"], hid, uid))
    return jsonify(ok=True)


@app.delete("/api/holdings/<int:hid>")
@login_required
def api_delete_holding(hid):
    db.execute("DELETE FROM holdings WHERE id=? AND user_id=?", (hid, current_user()["id"]))
    return jsonify(ok=True)


@app.post("/api/holdings/load-sample")
@login_required
def api_load_sample():
    key = (request.get_json(silent=True) or {}).get("persona", "moderate")
    if key not in dl.PERSONAS:
        return err("Unknown sample investor.")
    seed_persona(current_user()["id"], key)
    return jsonify(ok=True)


def safe_cell(v):
    """Stop spreadsheet formula injection when users open exported CSV files in Excel."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@app.get("/export/holdings.csv")
@login_required
def export_holdings():
    st = load_state(current_user()["id"])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name", "symbol", "category", "quantity", "buy_price", "latest_price", "current_value", "unrealized_pl", "return_pct", "price_source"])
    for h in st["enriched"]:
        w.writerow([safe_cell(h["name"]), h["symbol"] or "", h["category"], h["quantity"], h["buy_price"],
                    "" if h["latest_price"] is None else h["latest_price"],
                    "" if h["value"] is None else round(h["value"], 2), "" if h["pl"] is None else round(h["pl"], 2),
                    "" if h["ret"] is None else round(h["ret"], 2), h["price_source"]])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=holdings.csv"})


@app.get("/export/template.csv")
def export_template():
    body = ("name,symbol,category,quantity,buy_price,latest_price\n"
            "Sample Large-Cap Stock,EQ-LARGE,Equity,10,2400,\n"
            "My Fixed Deposit,,Cash,1,100000,104000\n")
    return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=holdings_template.csv"})


@app.post("/api/holdings/import")
@login_required
def api_import():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".csv"):
        return err("Please choose a .csv file.")
    try:
        text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return err("The file is not valid UTF-8 text. Re-save it as CSV (UTF-8).")
    reader = csv.DictReader(io.StringIO(text))
    headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = {"name", "category", "quantity", "buy_price"} - set(headers)
    if missing:
        return err("Missing required column(s): " + ", ".join(sorted(missing)) + ". Download the template for the correct format.")
    good, errors = [], []
    for i, raw in enumerate(reader, start=2):
        if i > 501:
            errors.append("Only the first 500 rows are read.")
            break
        row = {(k or "").strip().lower(): v for k, v in raw.items()}
        clean, errs = validate_holding(row)
        if errs:
            errors.append(f"Row {i}: " + " ".join(errs.values()))
        else:
            good.append(clean)
    if not good:
        return err("No valid rows found.", details=errors[:20])
    uid = current_user()["id"]
    if request.form.get("mode") == "replace":
        db.execute("DELETE FROM holdings WHERE user_id=?", (uid,))
    for c in good:
        db.execute("INSERT INTO holdings(user_id,name,symbol,category,quantity,buy_price,latest_price) VALUES (?,?,?,?,?,?,?)",
                   (uid, c["name"], c["symbol"], c["category"], c["quantity"], c["buy_price"], c["latest_price"]))
    return jsonify(imported=len(good), skipped=len(errors), details=errors[:20])

# ------------------------------------------------------------------ API: allocation, performance, history


@app.post("/api/allocation/compare")
@login_required
def api_alloc_compare():
    target = (request.get_json(silent=True) or {}).get("target", {})
    clean = {}
    for c in engine.CATEGORIES:
        v = num(target.get(c, 0)) if str(target.get(c, "")).strip() != "" else 0
        if v is None or v < 0 or v > 100:
            return err(f"{c}: enter a percentage between 0 and 100.")
        clean[c] = v
    if abs(sum(clean.values()) - 100) > 0.5:
        return err(f"Target percentages add up to {sum(clean.values()):.1f}%. They must add up to 100%.")
    return jsonify(engine.compare_with_target(load_state(current_user()["id"])["alloc"]["by_category"], clean))


@app.get("/api/performance")
@login_required
def api_performance():
    rng, freq = request.args.get("range", "1Y"), request.args.get("freq", "D")
    if rng not in engine.RANGES or freq not in engine.FREQS:
        return err("Invalid range or frequency.")
    return jsonify(engine.performance(load_state(current_user()["id"])["enriched"], dl.load_prices(), rng, freq))


@app.get("/api/history/<symbol>")
@login_required
def api_history(symbol):
    rng, freq = request.args.get("range", "1Y"), request.args.get("freq", "D")
    if rng not in engine.RANGES or freq not in engine.FREQS:
        return err("Invalid range or frequency.")
    h = engine.asset_history(symbol.upper(), dl.load_prices(), rng, freq)
    return jsonify(h) if h else err("No data for that symbol.", 404)

# ------------------------------------------------------------------ API: risk profile + ML


@app.get("/api/profile")
@login_required
def api_profile():
    st = load_state(current_user()["id"])
    return jsonify(profile=st["profile"], risk=st["risk"], questions=engine.QUESTIONS, experience=engine.EXPERIENCE,
                   goals=engine.GOALS, preferred=engine.PREFERRED, is_demo=bool(current_user()["is_demo"]))


@app.post("/api/profile")
@login_required
def api_save_profile():
    uid = current_user()["id"]
    d = request.get_json(silent=True) or {}
    old = get_profile(uid)
    errs = {}
    name = str(d.get("display_name") or "").strip()[:40]
    exp = d.get("experience")
    if exp not in engine.EXPERIENCE:
        errs["experience"] = "Choose your investing experience."
    hz = num(d.get("horizon_years"))
    if hz is None or not (0.1 <= hz <= 60):
        errs["horizon_years"] = "Enter a time horizon between 0.1 and 60 years."
    pref = d.get("preferred_risk") if d.get("preferred_risk") in engine.PREFERRED else None
    goal = str(d.get("goal") or "").strip()[:100] or None
    answers = {}
    for q in engine.QUESTIONS:
        v = (d.get("answers") or {}).get(q["id"])
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < len(q["options"]):
            answers[q["id"]] = v
        else:
            errs[q["id"]] = "Please choose an answer."
    if errs:
        return err("Some answers are missing or invalid.", fields=errs)
    risk = save_profile(uid, dict(display_name=name, experience=exp, goal=goal, horizon_years=hz, preferred_risk=pref, answers=answers))
    return jsonify(risk=risk, previous_category=old["risk_category"], previous_score=old["risk_score"])


@app.get("/api/ml/risk")
@login_required
def api_ml():
    risk = load_state(current_user()["id"])["risk"]
    if not risk:
        return err("Complete the risk questionnaire first.")
    try:
        return jsonify(**engine.ml_predict(risk["points"]), rule_category=risk["category"])
    except ImportError:
        return err("scikit-learn is not installed. Run: pip install scikit-learn", 501)

# ------------------------------------------------------------------ API: insights


@app.get("/api/insights")
@login_required
def api_insights():
    st = load_state(current_user()["id"])
    p = st["profile"]
    ins = engine.generate_insights(st["enriched"], st["summary"], st["alloc"], st["risk"], p, p["conc_threshold"], dl.data_status())
    return jsonify(insights=ins, threshold=p["conc_threshold"], disclaimer="Educational insights from simple rules. Not buy/sell advice.")

# ------------------------------------------------------------------ API: compare + scenarios


@app.get("/api/catalog")
@login_required
def api_catalog():
    return jsonify(assets=[dict(symbol=k, name=v["name"], category=v["category"], expense=v["expense"], min_invest=v["min_invest"])
                           for k, v in dl.CATALOG.items() if k != dl.BENCHMARK], label=dl.DATA_LABEL)


def _range_freq(d):
    rng, freq = d.get("range", "1Y"), d.get("freq", "W")
    return (rng, freq) if rng in engine.RANGES and freq in engine.FREQS else (None, None)


@app.post("/api/compare/assets")
@login_required
def api_compare_assets():
    d = request.get_json(silent=True) or {}
    syms = [s for s in dict.fromkeys(d.get("symbols") or []) if s in dl.CATALOG and s != dl.BENCHMARK]
    rng, freq = _range_freq(d)
    if not (2 <= len(syms) <= 6):
        return err("Choose between 2 and 6 assets to compare.")
    if not rng:
        return err("Invalid range or frequency.")
    res = engine.compare_assets(syms, dl.load_prices(), rng, freq)
    return jsonify(res) if res else err("Not enough data for that period.")


@app.post("/api/compare/allocations")
@login_required
def api_compare_allocs():
    d = request.get_json(silent=True) or {}
    rng, freq = _range_freq(d)
    allocs = d.get("allocations") or []
    if not rng:
        return err("Invalid range or frequency.")
    if not (1 <= len(allocs) <= 4):
        return err("Provide 1 to 4 allocations.")
    clean = []
    for a in allocs:
        w = {}
        for s, v in (a.get("weights") or {}).items():
            x = num(v)
            if s not in dl.CATALOG or s == dl.BENCHMARK:
                return err(f"Unknown symbol: {s}")
            if x is None or x < 0 or x > 100:
                return err(f"{a.get('name', 'Allocation')}: weights must be between 0 and 100.")
            if x > 0:
                w[s] = x
        total = sum(w.values())
        if abs(total - 100) > 0.5:
            return err(f"{a.get('name', 'Allocation')} adds up to {total:.1f}%. It must add up to 100%.")
        clean.append(dict(name=str(a.get("name") or "Portfolio")[:40], weights=w))
    res = engine.compare_allocations(clean, dl.load_prices(), rng, freq)
    return jsonify(res) if res else err("Not enough data for that period.")


@app.get("/api/current-weights")
@login_required
def api_current_weights():
    st = load_state(current_user()["id"])
    items = [h for h in st["enriched"] if h["symbol"] in dl.CATALOG and h["value"]]
    total = sum(h["value"] for h in items)
    w = {}
    for h in items:
        w[h["symbol"]] = round(w.get(h["symbol"], 0) + h["value"] / total * 100, 1)
    return jsonify(weights=w, excluded=len(st["enriched"]) - len(items))


@app.get("/api/scenarios")
@login_required
def api_scenarios():
    rows = db.query("SELECT id,name,kind,data,created_at FROM scenarios WHERE user_id=? ORDER BY id DESC", (current_user()["id"],))
    for r in rows:
        r["data"] = json.loads(r["data"])
    return jsonify(scenarios=rows)


@app.post("/api/scenarios")
@login_required
def api_save_scenario():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name") or "").strip()[:60]
    if not name:
        return err("Give the scenario a name.")
    if d.get("kind") not in ("allocation", "goal"):
        return err("Unknown scenario type.")
    raw = json.dumps(d.get("data") or {})
    if len(raw) > 20000:
        return err("Scenario is too large.")
    uid = current_user()["id"]
    if db.query("SELECT COUNT(*) c FROM scenarios WHERE user_id=?", (uid,), one=True)["c"] >= 50:
        return err("Scenario limit (50) reached. Delete some first.")
    return jsonify(id=db.execute("INSERT INTO scenarios(user_id,name,kind,data) VALUES (?,?,?,?)", (uid, name, d["kind"], raw))), 201


@app.delete("/api/scenarios/<int:sid>")
@login_required
def api_del_scenario(sid):
    db.execute("DELETE FROM scenarios WHERE id=? AND user_id=?", (sid, current_user()["id"]))
    return jsonify(ok=True)


@app.post("/api/scenarios/clear")
@login_required
def api_clear_scenarios():
    db.execute("DELETE FROM scenarios WHERE user_id=?", (current_user()["id"],))
    return jsonify(ok=True)

# ------------------------------------------------------------------ API: goal planner


@app.post("/api/goal")
@login_required
def api_goal():
    d = request.get_json(silent=True) or {}
    target, years, rate = num(d.get("target")), num(d.get("years")), num(d.get("rate"))
    current = num(d.get("current")) if str(d.get("current", "")).strip() else 0.0
    timing = d.get("timing", "start")
    if target is None or target <= 0 or target > 1e12:
        return err("Target amount must be greater than 0.")
    if years is None or not (0.5 <= years <= 50):
        return err("Time horizon must be between 0.5 and 50 years.")
    if rate is None or not (0 <= rate <= 30):
        return err("Assumed return must be between 0% and 30% a year.")
    if current is None or current < 0:
        return err("Current savings must be 0 or more.")
    if timing not in ("start", "end"):
        return err("Invalid payment timing.")
    return jsonify(engine.goal_plan(target, years, rate, current, timing))

# ------------------------------------------------------------------ API: watchlist + alerts


@app.get("/api/watchlist")
@login_required
def api_watchlist():
    prices = dl.load_prices()
    items = []
    for r in db.query("SELECT symbol FROM watchlist WHERE user_id=? ORDER BY id", (current_user()["id"],)):
        s = r["symbol"]
        if s not in prices.columns:
            items.append(dict(symbol=s, name=s, unavailable=True))
            continue
        ser = prices[s].dropna()
        def chg(days):
            past = ser[ser.index <= ser.index[-1] - pd.Timedelta(days=days)]
            return None if past.empty else round(float((ser.iloc[-1] / past.iloc[-1] - 1) * 100), 2)
        meta = dl.CATALOG[s]
        items.append(dict(symbol=s, name=meta["name"], category=meta["category"], price=round(float(ser.iloc[-1]), 2),
                          change_1d=chg(1), change_1m=chg(30), change_1y=chg(365),
                          last_updated=ser.index[-1].strftime("%Y-%m-%d"), source="Simulated sample data"))
    return jsonify(items=items, label=dl.DATA_LABEL)


@app.post("/api/watchlist")
@login_required
def api_watch_add():
    sym = str((request.get_json(silent=True) or {}).get("symbol", "")).upper()
    if sym not in dl.CATALOG or sym == dl.BENCHMARK:
        return err("Choose an asset from the list.")
    db.execute("INSERT OR IGNORE INTO watchlist(user_id,symbol) VALUES (?,?)", (current_user()["id"], sym))
    return jsonify(ok=True), 201


@app.delete("/api/watchlist/<symbol>")
@login_required
def api_watch_del(symbol):
    db.execute("DELETE FROM watchlist WHERE user_id=? AND symbol=?", (current_user()["id"], symbol.upper()))
    return jsonify(ok=True)


def evaluate_alerts(uid):
    st = load_state(uid)
    cur = st["profile"]["currency"]
    latest = dl.latest_prices()
    srcs = {h["price_source"] for h in st["enriched"] if h["value"] is not None}
    basis_p = "Based on " + (" + ".join(sorted(srcs)) if srcs else "no priced holdings") + " (not live data)"
    out = []
    for a in db.query("SELECT * FROM alerts WHERE user_id=? ORDER BY id DESC", (uid,)):
        k, t, trig, msg, basis = a["kind"], a["threshold"], False, "", basis_p
        if k in ("value_below", "value_above"):
            v = st["summary"]["value"]
            trig = v < t if k == "value_below" else v > t
            msg = f"Portfolio value is {money(v, cur)}; your level is {money(t, cur)}."
        elif k == "weight_above":
            w = next((c["weight"] for c in st["alloc"]["by_category"] if c["category"] == a["category"]), 0)
            trig = w > t
            msg = f"{a['category']} is {w:.1f}% of the portfolio; your level is {t:g}%."
        else:
            p = latest.get(a["symbol"])
            basis = "Based on simulated sample price (not live data)"
            if p is None:
                continue
            trig = p < t if k == "price_below" else p > t
            msg = f"{a['symbol']} sample price is {p:,.2f}; your level is {t:,.2f}."
        out.append(dict(id=a["id"], kind=k, label=ALERT_KINDS[k], symbol=a["symbol"], category=a["category"], threshold=t,
                        triggered=bool(trig), message=msg + " This is a reminder you set, not a trading instruction.", basis=basis))
    return out


@app.get("/api/alerts")
@login_required
def api_alerts():
    return jsonify(alerts=evaluate_alerts(current_user()["id"]), kinds=ALERT_KINDS, categories=engine.CATEGORIES)


@app.post("/api/alerts")
@login_required
def api_alert_add():
    d = request.get_json(silent=True) or {}
    kind, t = d.get("kind"), num(d.get("threshold"))
    if kind not in ALERT_KINDS:
        return err("Choose an alert type.")
    if t is None or t <= 0:
        return err("Threshold must be a number greater than 0.")
    sym = cat = None
    if kind == "weight_above":
        cat = d.get("category")
        if cat not in engine.CATEGORIES or t > 100:
            return err("Choose a category and a percentage up to 100.")
    elif kind.startswith("price"):
        sym = str(d.get("symbol", "")).upper()
        if sym not in dl.CATALOG or sym == dl.BENCHMARK:
            return err("Choose an asset.")
    uid = current_user()["id"]
    if db.query("SELECT COUNT(*) c FROM alerts WHERE user_id=?", (uid,), one=True)["c"] >= 30:
        return err("Alert limit (30) reached.")
    return jsonify(id=db.execute("INSERT INTO alerts(user_id,kind,symbol,category,threshold) VALUES (?,?,?,?,?)", (uid, kind, sym, cat, t))), 201


@app.delete("/api/alerts/<int:aid>")
@login_required
def api_alert_del(aid):
    db.execute("DELETE FROM alerts WHERE id=? AND user_id=?", (aid, current_user()["id"]))
    return jsonify(ok=True)

# ------------------------------------------------------------------ API: settings + data management


@app.get("/api/settings")
@login_required
def api_settings():
    p = get_profile(current_user()["id"])
    return jsonify(currency=p["currency"], conc_threshold=p["conc_threshold"], default_range=p["default_range"],
                   currencies=list(CURRENCIES), ranges=list(engine.RANGES), status=dl.data_status(),
                   is_demo=bool(current_user()["is_demo"]), username=current_user()["username"])


@app.post("/api/settings")
@login_required
def api_save_settings():
    d = request.get_json(silent=True) or {}
    t = num(d.get("conc_threshold"))
    if d.get("currency") not in CURRENCIES:
        return err("Unsupported currency.")
    if d.get("default_range") not in engine.RANGES:
        return err("Unsupported date range.")
    if t is None or not (5 <= t <= 100):
        return err("Concentration threshold must be between 5% and 100%.")
    get_profile(current_user()["id"])
    db.execute("UPDATE profiles SET currency=?, conc_threshold=?, default_range=? WHERE user_id=?",
               (d["currency"], t, d["default_range"], current_user()["id"]))
    return jsonify(ok=True)


@app.post("/api/demo/reset")
@login_required
def api_demo_reset():
    u = current_user()
    if not u["is_demo"]:
        return err("Only demo sessions can be reset. Your saved data is untouched.")
    seed_persona(u["id"], u["persona"] or "moderate")
    return jsonify(ok=True)


@app.get("/api/export/all")
@login_required
def api_export_all():
    uid = current_user()["id"]
    p = get_profile(uid)
    data = dict(exported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z", note="Educational prototype export. Sample prices are simulated.",
                profile={k: p[k] for k in ("display_name", "experience", "goal", "horizon_years", "preferred_risk", "answers",
                                           "risk_score", "risk_category", "currency", "conc_threshold", "default_range")},
                holdings=db.query("SELECT name,symbol,category,quantity,buy_price,latest_price FROM holdings WHERE user_id=?", (uid,)),
                watchlist=[r["symbol"] for r in db.query("SELECT symbol FROM watchlist WHERE user_id=?", (uid,))],
                alerts=db.query("SELECT kind,symbol,category,threshold FROM alerts WHERE user_id=?", (uid,)))
    return Response(json.dumps(data, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=my_investment_data.json"})


@app.post("/api/account/delete")
@login_required
def api_delete_account():
    u = current_user()
    if not u["is_demo"]:
        pw = (request.get_json(silent=True) or {}).get("password", "")
        if not check_password_hash(u["password_hash"], pw):
            return err("Password is incorrect.", 403)
    db.execute("DELETE FROM users WHERE id=?", (u["id"],))
    session.clear()
    return jsonify(ok=True)


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return render_template("login.html", error="Page not found.", personas=dl.PERSONAS, mode="login"), 404


@app.errorhandler(413)
def too_big(_e):
    return jsonify(error="File is too large (limit 2 MB)."), 413


if __name__ == "__main__":
    app.run(debug=True, port=5000)
