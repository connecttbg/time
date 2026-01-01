
import os
import io
import zipfile
import tempfile
import errno
import shutil
import smtplib
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from flask import Flask, request, redirect, url_for, send_file, abort, flash, render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import text as sql_text, and_

# --- Flask & DB config ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, "app.db")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.getenv("SECRET_KEY", "dev-key-change-me")

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_FILE}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# --- Models ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active_u = db.Column(db.Boolean, default=True)

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)

    @property
    def is_active(self):
        return self.is_active_u


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, default=True)


class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    minutes = db.Column(db.Integer, nullable=False, default=0)
    is_extra = db.Column(db.Boolean, default=False)
    is_overtime = db.Column(db.Boolean, default=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="entries")
    project = db.relationship("Project", backref="entries")


class Cost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    cost_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="costs")


class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=True)

    # DRAFT -> SUBMITTED -> APPROVED
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    decided_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id], backref="leave_requests")
    decided_by_user = db.relationship("User", foreign_keys=[decided_by])



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- Helpers ---
def fmt_hhmm(minutes: int) -> str:
    minutes = minutes or 0
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

def parse_hhmm(value: str) -> int:
    if not value:
        return 0
    v = value.strip().lower()
    if v.isdigit():
        return int(v)
    if 'h' in v:
        try:
            left, *rest = v.split('h')
            h = int(left) if left else 0
            m = int(rest[0]) if rest and rest[0] else 0
            return h*60 + m
        except Exception:
            pass
    if ':' in v:
        hh, mm = v.split(':', 1)
        h = int(hh) if hh else 0
        m = int(mm) if mm else 0
        return h*60 + m
    try:
        f = float(v.replace(',', '.'))
        return int(round(f*60))
    except Exception:
        return 0

def month_bounds(d: date):
    first = d.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1, day=1)
    else:
        nxt = first.replace(month=first.month + 1, day=1)
    last = nxt - timedelta(days=1)
    return first, last

def require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)

def ensure_db_file():
    os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
    with app.app_context():
        db.create_all()
        try:
            db.session.execute(sql_text("SELECT 1"))
        except Exception:
            pass


# --- Init DB (safe) ---
def init_db():
    ensure_db_file()
    with app.app_context():
        try:
            has_user = db.session.query(User.id).first()
        except Exception:
            db.create_all()
            has_user = None

        if not has_user:
            admin = User(name="Administrator", email="admin@local", is_admin=True)
            admin.set_password("admin123")
            db.session.add(admin)

        if not db.session.query(Project.id).first():
            db.session.add(Project(name="Projekt domyślny", is_active=True))

        db.session.commit()

init_db()


# --- Base layout (jasny) ---
BASE = """
<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title or 'EKKO NOR AS – Rejestrator czasu pracy' }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root { color-scheme: light; }
    body{ background:#f5f7fb; color:#1f2937; }
    .navbar{ background:#ffffff; border-bottom:1px solid #e5e7eb; }
    .card{ background:#ffffff; border:1px solid #e5e7eb; border-radius:14px; }
    .form-control,.form-select,.form-check-input{ background:#ffffff; color:#111827; border:1px solid #d1d5db; }
    .btn-primary{ background:#2563eb; border-color:#2563eb; }
    .btn-outline-primary{ border-color:#2563eb; color:#2563eb; }
    .btn-outline-primary:hover{ background:#2563eb; color:white; }
    .table{ color:#111827; }
    .table thead{ background:#f3f4f6; }
    .badge-soft{ background:#eef2ff; border:1px solid #c7d2fe; color:#3730a3; }
    .brand-logo{ height:36px; }
    .brand-big{ max-width:180px; display:block; margin:0 auto 16px; }
    a{ color:#2563eb; }
    .container-narrow{ max-width:1100px; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-light mb-4">
  <div class="container-fluid">
    <a class="navbar-brand d-flex align-items-center" href="{{ url_for('dashboard') if current_user.is_authenticated else url_for('login') }}">
      <img src="{{ url_for('static', filename='ekko_logo.png') }}" class="brand-logo me-2" alt="logo">
      
    </a>
    {% if current_user.is_authenticated %}
    <div class="ms-auto d-flex align-items-center gap-2">
      {% if current_user.is_admin %}
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_overview') }}">Admin</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_users') }}">Pracownicy</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_projects') }}">Projekty</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_entries') }}">Godziny (admin)</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_costs') }}">Koszty (admin)</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_reports') }}">Raport</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_backup') }}">Kopie</a>
      {% endif %}
      <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('leaves') }}">Urlopy</a>
      <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('user_costs') }}">Koszty</a>
      <a class="badge badge-soft px-3 py-2 text-decoration-none" href="{{ url_for('user_summary') }}">{{ current_user.name }}</a>
      <a class="btn btn-sm btn-danger" href="{{ url_for('logout') }}">Wyloguj</a>
    </div>
    {% endif %}
  </div>
</nav>

<div class="container container-narrow mb-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
  {{ body|safe }}
</div>

<div class="text-center mt-4 text-muted" style="font-size:12px;">aplikacja utworzona przez dataconnect.no</div>
</body>
</html>
"""

def layout(title, body):
    return render_template_string(BASE, title=title, body=body, fmt=fmt_hhmm)




@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))
# --- Auth ---
@app.route("/", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(pw) and user.is_active:
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Nieprawidłowy login lub hasło albo konto nieaktywne.")
        return redirect(url_for("login"))

    body = render_template_string("""
<div class="row justify-content-center">
  <div class="col-md-5">
    <div class="text-center mb-3">
      <img src="{{ url_for('static', filename='ekko_logo.png') }}" class="brand-big" alt="logo">
      <h4 class="mb-0">EKKO NOR AS</h4>
      <div class="text-muted">Rejestrator czasu pracy</div>
    </div>
    <div class="card p-3">
      <form method="post">
        <div class="mb-3">
          <label class="form-label">E-mail</label>
          <input class="form-control" type="email" name="email" placeholder="np. imie@firma.no" required>
        </div>
        <div class="mb-3">
          <label class="form-label">Hasło</label>
          <input class="form-control" type="password" name="password" required>
        </div>
        <button class="btn btn-primary w-100">Zaloguj</button>
      </form>
    </div>
  </div>
</div>
""")
    return layout("Logowanie", body)





# --- Dashboard (user) ---
@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        work_date_str = request.form.get("work_date")
        project_id = int(request.form.get("project_id"))
        hhmm = request.form.get("hhmm", "0")
        minutes = parse_hhmm(hhmm)
        is_extra = bool(request.form.get("is_extra"))
        is_overtime = bool(request.form.get("is_overtime"))
        note = request.form.get("note") or ""

        # Konwersja daty z formularza
        try:
            work_date = datetime.strptime(work_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Nieprawidłowa data.")
            return redirect(url_for("dashboard"))

        # Ograniczenie 48h tylko dla zwykłych użytkowników (nie adminów)
        if not getattr(current_user, "is_admin", False):
            now = datetime.now()
            # Traktujemy koniec dnia roboczego jako granicę (23:59:59 danego dnia)
            end_of_work_date = datetime.combine(work_date, datetime.max.time())
            if end_of_work_date < now - timedelta(hours=48):
                flash("Godziny zostaly zablokowane poniewaz mozesz dodawac je maksymalnie do 48h skontaktuj sie z Darkiem +4746572904.")
                return redirect(url_for("dashboard"))

        e = Entry(
            user_id=current_user.id,
            project_id=project_id,
            work_date=work_date,
            minutes=minutes,
            is_extra=is_extra,
            is_overtime=is_overtime,
            note=note,
        )
        db.session.add(e)
        db.session.commit()
        flash("Dodano wpis.")
        return redirect(url_for("dashboard"))

    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    today = date.today()
    m_from, m_to = month_bounds(today)
    entries = (
        Entry.query.filter(
            Entry.user_id == current_user.id,
            Entry.work_date >= m_from,
            Entry.work_date <= m_to,
        )
        .order_by(Entry.work_date.desc(), Entry.id.desc())
        .all()
    )
    tot = sum(e.minutes for e in entries)
    tot_extra = sum(e.minutes for e in entries if e.is_extra)
    tot_ot = sum(e.minutes for e in entries if e.is_overtime)

    body = render_template_string("""
<div class="row g-3">
  <div class="col-12">
    <div class="card p-3">
      <h5 class="mb-3">Dodaj godziny</h5>
      <form class="row g-2" method="post">
        <div class="col-md-3">
          <label class="form-label">Data</label>
          <input class="form-control" type="date" name="work_date" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-3">
          <label class="form-label">Projekt</label>
          <select class="form-select" name="project_id" required>
            {% for p in projects %}
              <option value="{{ p.id }}">{{ p.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-2">
          <label class="form-label">Czas (HH:MM)</label>
          <input class="form-control" type="text" name="hhmm" placeholder="np. 1:30" value="1:00" required>
        </div>
        <div class="col-md-2 d-flex align-items-end gap-3">
          <div class="form-check">
            <input class="form-check-input" type="checkbox" name="is_extra" id="extra">
            <label class="form-check-label" for="extra">Extra</label>
          </div>
          <div class="form-check">
            <input class="form-check-input" type="checkbox" name="is_overtime" id="ot">
            <label class="form-check-label" for="ot">Nadgodziny</label>
          </div>
        </div>
        <div class="col-md-12">
          <label class="form-label">Notatka</label>
          <input class="form-control" type="text" name="note" placeholder="opcjonalnie">
        </div>
        <div class="col-12">
          <button class="btn btn-primary">Zapisz</button>
        </div>
      </form>
    </div>
  </div>

  <div class="col-12">
    <div class="card p-3">
      <div class="d-flex justify-content-between align-items-center">
        <h5 class="mb-0">Moje wpisy – {{ m_from.isoformat() }} → {{ m_to.isoformat() }}</h5>
      </div>
      <div class="table-responsive mt-3">
        <table class="table table-sm align-middle">
          <thead>
            <tr><th>Data</th><th>Projekt</th><th>Notatka</th><th>Godziny</th><th>Extra</th><th>OT</th><th class="text-end">Akcje</th></tr>
          </thead>
          <tbody>
            {% for e in entries %}
            <tr>
              <td>{{ e.work_date.isoformat() }}</td>
              <td>{{ e.project.name }}</td>
              <td>{{ e.note or '' }}</td>
              <td>{{ fmt(e.minutes) }}</td>
              <td>{% if e.is_extra %}✔{% else %}-{% endif %}</td>
              <td>{% if e.is_overtime %}✔{% else %}-{% endif %}</td>
              <td class="text-end">
                <a class="btn btn-sm btn-outline-primary" href="{{ url_for('edit_entry', entry_id=e.id) }}">Edytuj</a>
                <form class="d-inline" method="post" action="{{ url_for('delete_entry', entry_id=e.id) }}" onsubmit="return confirm('Usunąć wpis?')">
                  <button class="btn btn-sm btn-outline-danger">Usuń</button>
                </form>
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      <div class="mt-2">
        <span class="me-3">Razem: <strong>{{ fmt(tot) }}</strong></span>
        <span class="me-3">Extra: <strong>{{ fmt(tot_extra) }}</strong></span>
        <span class="me-3">Nadgodziny: <strong>{{ fmt(tot_ot) }}</strong></span>
      </div>
    </div>
  </div>
</div>
""", projects=projects, entries=entries, fmt=fmt_hhmm, m_from=m_from, m_to=m_to, tot=tot, tot_extra=tot_extra, tot_ot=tot_ot, date=date)
    return layout("Panel", body)


# --- Edit/Delete entries (user & admin) ---
@app.route("/entry/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id):
    e = Entry.query.get_or_404(entry_id)
    if not (current_user.is_admin or e.user_id == current_user.id):
        abort(403)

    if request.method == "POST":
        e.work_date = datetime.strptime(request.form.get("work_date"), "%Y-%m-%d").date()
        e.project_id = int(request.form.get("project_id"))
        e.minutes = parse_hhmm(request.form.get("hhmm", "0"))
        e.is_extra = bool(request.form.get("is_extra"))
        e.is_overtime = bool(request.form.get("is_overtime"))
        e.note = request.form.get("note") or ""
        db.session.commit()
        flash("Zapisano zmiany.")
        return redirect(url_for("dashboard" if not current_user.is_admin else "admin_entries"))

    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    hhmm_value = fmt_hhmm(e.minutes)
    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Edytuj wpis</h5>
  <form class="row g-2" method="post">
    <div class="col-md-3">
      <label class="form-label">Data</label>
      <input class="form-control" type="date" name="work_date" value="{{ e.work_date.isoformat() }}" required>
    </div>
    <div class="col-md-3">
      <label class="form-label">Projekt</label>
      <select class="form-select" name="project_id">
        {% for p in projects %}
          <option value="{{ p.id }}" {% if p.id == e.project_id %}selected{% endif %}>{{ p.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-md-2">
      <label class="form-label">Czas (HH:MM)</label>
      <input class="form-control" type="text" name="hhmm" value="{{ hhmm_value }}" required>
    </div>
    <div class="col-md-2 d-flex align-items-end gap-3">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_extra" id="extra" {% if e.is_extra %}checked{% endif %}>
        <label class="form-check-label" for="extra">Extra</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_overtime" id="ot" {% if e.is_overtime %}checked{% endif %}>
        <label class="form-check-label" for="ot">Nadgodziny</label>
      </div>
    </div>
    <div class="col-12">
      <label class="form-label">Notatka</label>
      <input class="form-control" type="text" name="note" value="{{ e.note or '' }}">
    </div>
    <div class="col-12">
      <button class="btn btn-primary">Zapisz</button>
      <a class="btn btn-outline-secondary" href="{{ url_for('dashboard') }}">Anuluj</a>
    </div>
  </form>
</div>
""", e=e, projects=projects, hhmm_value=hhmm_value)
    return layout("Edytuj wpis", body)


@app.route("/entry/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_entry(entry_id):
    e = Entry.query.get_or_404(entry_id)
    if not (current_user.is_admin or e.user_id == current_user.id):
        abort(403)
    db.session.delete(e)
    db.session.commit()
    flash("Usunięto wpis.")
    return redirect(url_for("dashboard" if not current_user.is_admin else "admin_entries"))


# --- Admin: overview (monthly totals) ---
@app.route("/admin", methods=["GET"])
@login_required
def admin_overview():
    require_admin()
    ym = request.args.get("month")
    if not ym:
        today = date.today()
        ym = f"{today.year:04d}-{today.month:02d}"
    year, month = map(int, ym.split("-"))
    m_from = date(year, month, 1)
    m_to = (m_from.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    # poprzedni miesiąc
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_from = date(prev_year, prev_month, 1)
    prev_to = (prev_from.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    total = db.session.query(db.func.sum(Entry.minutes)).filter(
        Entry.work_date >= m_from, Entry.work_date <= m_to
    ).scalar() or 0

    users = User.query.filter_by(is_active_u=True).order_by(User.name).all()
    stats = []
    for u in users:
        curr_min = db.session.query(db.func.sum(Entry.minutes)).filter(
            Entry.user_id == u.id,
            Entry.work_date >= m_from,
            Entry.work_date <= m_to,
        ).scalar() or 0
        prev_min = db.session.query(db.func.sum(Entry.minutes)).filter(
            Entry.user_id == u.id,
            Entry.work_date >= prev_from,
            Entry.work_date <= prev_to,
        ).scalar() or 0
        stats.append({"user": u, "curr": curr_min, "prev": prev_min})

    body = render_template_string("""
<div class="card p-3 mb-3">
  <h5 class="mb-3">Podsumowanie miesiąca</h5>
  <form class="row g-2 mb-3" method="get">
    <div class="col-md-3">
      <label class="form-label">Miesiąc</label>
      <input class="form-control" type="month" name="month" value="{{ ym }}">
    </div>
    <div class="col-md-2 d-flex align-items-end">
      <button class="btn btn-outline-primary">Pokaż</button>
    </div>
  </form>
  <div class="display-6">{{ fmt(total) }}</div>
  <div class="text-muted">Łącznie zapisanych godzin w wybranym miesiącu</div>
</div>

<div class="card p-3">
  <h5 class="mb-3">Godziny pracowników</h5>
  <p class="small text-muted">
    Bieżący miesiąc: {{ ym }} &nbsp;&nbsp;|&nbsp;&nbsp;
    Poprzedni miesiąc: {{ prev_label }}
  </p>
  <div class="table-responsive">
    <table class="table table-sm table-striped align-middle">
      <thead>
        <tr>
          <th>Pracownik</th>
          <th>Godziny w tym miesiącu</th>
          <th>Godziny w poprzednim miesiącu</th>
        </tr>
      </thead>
      <tbody>
      {% for row in stats %}
        <tr>
          <td>{{ row.user.name }}</td>
          <td>{{ fmt(row.curr) }}</td>
          <td>{{ fmt(row.prev) }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""", ym=ym, total=total, stats=stats, fmt=fmt_hhmm,
       prev_label=f"{prev_year:04d}-{prev_month:02d}")
    return layout("Admin", body)


# --- Admin: users ---
@app.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_users():
    require_admin()

    if request.method == "POST" and request.form.get("action") == "create":
        name = request.form.get("name","").strip()
        email = (request.form.get("email","") or "").strip().lower()
        password = request.form.get("password","")
        is_admin = bool(request.form.get("is_admin"))
        if name and email and password:
            if not User.query.filter_by(email=email).first():
                u = User(name=name, email=email, is_admin=is_admin, is_active_u=True)
                u.set_password(password)
                db.session.add(u)
                db.session.commit()
                flash("Dodano pracownika.")
            else:
                flash("Taki e-mail już istnieje.")
        else:
            flash("Uzupełnij imię, e-mail i hasło.")
        return redirect(url_for("admin_users"))

    users = User.query.order_by(User.name).all()
    body = render_template_string("""
<div class="card p-3">
  <h5>Pracownicy</h5>

  <form class="row g-2 mb-3" method="post">
    <input type="hidden" name="action" value="create">
    <div class="col-md-3"><input class="form-control" name="name" placeholder="Imię i nazwisko" required></div>
    <div class="col-md-3"><input class="form-control" type="email" name="email" placeholder="E-mail" required></div>
    <div class="col-md-3"><input class="form-control" type="text" name="password" placeholder="Hasło startowe" required></div>
    <div class="col-md-2 d-flex align-items-center gap-2">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_admin" id="isadmin">
        <label class="form-check-label" for="isadmin">Admin</label>
      </div>
    </div>
    <div class="col-md-1"><button class="btn btn-primary w-100">Dodaj</button></div>
  </form>

  <div class="table-responsive">
    <table class="table table-sm align-middle">
      <thead><tr><th>Imię i nazwisko</th><th>E-mail</th><th>Rola</th><th>Status</th><th>Akcje</th></tr></thead>
      <tbody>
        {% for u in users %}
        <tr>
          <td>{{ u.name }}</td>
          <td>{{ u.email }}</td>
          <td>{% if u.is_admin %}Admin{% else %}Użytkownik{% endif %}</td>
          <td>{% if u.is_active %}Aktywny{% else %}Nieaktywny{% endif %}</td>
          <td class="text-nowrap">
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_user_edit', uid=u.id) }}">Edytuj</a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""", users=users)
    return layout("Pracownicy", body)


@app.route("/admin/users/<int:uid>", methods=["GET", "POST"])
@login_required
def admin_user_edit(uid):
    require_admin()
    u = User.query.get_or_404(uid)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            u.name = request.form.get("name","").strip()
            u.email = request.form.get("email","").strip().lower()
            u.is_admin = bool(request.form.get("is_admin"))
            u.is_active_u = bool(request.form.get("is_active"))
            db.session.commit()
            flash("Zapisano.")
            return redirect(url_for("admin_users"))
        elif action == "set_password":
            pw = request.form.get("password","")
            if pw:
                u.set_password(pw)
                db.session.commit()
                flash("Zmieniono hasło.")
            else:
                flash("Hasło nie może być puste.")
            return redirect(url_for("admin_user_edit", uid=u.id))

    body = render_template_string("""
<div class="card p-3">
  <h5>Edycja pracownika</h5>
  <form class="row g-2 mb-3" method="post">
    <input type="hidden" name="action" value="save">
    <div class="col-md-4"><label class="form-label">Imię i nazwisko</label>
      <input class="form-control" name="name" value="{{ u.name }}"></div>
    <div class="col-md-4"><label class="form-label">E-mail</label>
      <input class="form-control" type="email" name="email" value="{{ u.email }}"></div>
    <div class="col-md-4 d-flex align-items-end gap-3">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_admin" id="ea" {% if u.is_admin %}checked{% endif %}>
        <label class="form-check-label" for="ea">Admin</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_active" id="eact" {% if u.is_active %}checked{% endif %}>
        <label class="form-check-label" for="eact">Aktywny</label>
      </div>
      <button class="btn btn-primary ms-auto">Zapisz</button>
    </div>
  </form>

  <h6>Reset hasła</h6>
  <form class="row g-2" method="post">
    <input type="hidden" name="action" value="set_password">
    <div class="col-md-4"><input class="form-control" type="text" name="password" placeholder="Nowe hasło"></div>
    <div class="col-md-2"><button class="btn btn-outline-primary">Ustaw hasło</button></div>
  </form>
</div>
""", u=u)
    return layout("Edycja pracownika", body)


# --- Admin: projects (CRUD) ---
@app.route("/admin/projects", methods=["GET", "POST"])
@login_required
def admin_projects():
    require_admin()
    if request.method == "POST" and request.form.get("action") == "create":
        name = request.form.get("name").strip()
        if not name:
            flash("Nazwa nie może być pusta.")
        elif Project.query.filter_by(name=name).first():
            flash("Projekt o takiej nazwie już istnieje.")
        else:
            db.session.add(Project(name=name, is_active=True))
            db.session.commit()
            flash("Dodano projekt.")
        return redirect(url_for("admin_projects"))

    projs = Project.query.order_by(Project.is_active.desc(), Project.name.asc()).all()
    body = render_template_string("""
<div class="card p-3">
  <div class="d-flex justify-content-between align-items-center">
    <h5 class="mb-0">Projekty</h5>
    <form class="d-flex" method="post">
      <input type="hidden" name="action" value="create">
      <input class="form-control me-2" name="name" placeholder="Nazwa projektu" required>
      <button class="btn btn-primary">Dodaj</button>
    </form>
  </div>
  <div class="table-responsive mt-3">
    <table class="table table-sm align-middle">
      <thead><tr><th style="width:55%">Nazwa</th><th style="width:20%">Aktywny</th><th class="text-end" style="width:25%">Akcje</th></tr></thead>
      <tbody>
      {% for p in projs %}
        <tr>
          <td>
            <form class="d-flex" method="post" action="{{ url_for('admin_project_update', pid=p.id) }}">
              <input class="form-control form-control-sm me-2" name="name" value="{{ p.name }}" required>
              <button class="btn btn-sm btn-outline-primary">Zmień nazwę</button>
            </form>
          </td>
          <td>
            <form method="post" action="{{ url_for('admin_project_toggle', pid=p.id) }}">
              <input type="hidden" name="is_active" value="{{ 1 if not p.is_active else 0 }}">
              <button class="btn btn-sm {% if p.is_active %}btn-success{% else %}btn-secondary{% endif %}">
                {% if p.is_active %}Aktywny{% else %}Nieaktywny{% endif %}
              </button>
            </form>
          </td>
          <td class="text-end">
            <form class="d-inline" method="post" action="{{ url_for('admin_project_delete', pid=p.id) }}" onsubmit="return confirm('Usunąć projekt? (wpisy pozostaną)')">
              <button class="btn btn-sm btn-outline-danger">Usuń</button>
            </form>
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""", projs=projs)
    return layout("Projekty", body)

@app.route("/admin/projects/<int:pid>/update", methods=["POST"])
@login_required
def admin_project_update(pid):
    require_admin()
    p = Project.query.get_or_404(pid)
    new_name = (request.form.get("name") or "").strip()
    if not new_name:
        flash("Nazwa nie może być pusta.")
    elif Project.query.filter(Project.id != pid, Project.name == new_name).first():
        flash("Projekt o takiej nazwie już istnieje.")
    else:
        p.name = new_name
        db.session.commit()
        flash("Zmieniono nazwę projektu.")
    return redirect(url_for("admin_projects"))

@app.route("/admin/projects/<int:pid>/toggle", methods=["POST"])
@login_required
def admin_project_toggle(pid):
    require_admin()
    p = Project.query.get_or_404(pid)
    want_active = request.form.get("is_active")
    if want_active is not None:
        p.is_active = True if str(want_active) == "1" else False
    else:
        p.is_active = not p.is_active
    db.session.commit()
    return redirect(url_for("admin_projects"))

@app.route("/admin/projects/<int:pid>/delete", methods=["POST"])
@login_required
def admin_project_delete(pid):
    require_admin()
    p = Project.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    flash("Usunięto projekt.")
    return redirect(url_for("admin_projects"))


# --- Admin: entries (full add/edit/delete + filter) ---
def _all_users_ordered():
    return User.query.order_by(User.name.asc()).all()

@app.route("/admin/entries", methods=["GET", "POST"])
@login_required
def admin_entries():
    require_admin()

    if request.method == "POST":
        uid = int(request.form.get("user_id"))
        pid = int(request.form.get("project_id"))
        work_date = datetime.strptime(request.form.get("work_date"), "%Y-%m-%d").date()
        minutes = parse_hhmm(request.form.get("hhmm", "0"))
        is_extra = bool(request.form.get("is_extra"))
        is_ot = bool(request.form.get("is_overtime"))
        note = request.form.get("note") or ""

        db.session.add(Entry(
            user_id=uid, project_id=pid, work_date=work_date,
            minutes=minutes, is_extra=is_extra, is_overtime=is_ot, note=note
        ))
        db.session.commit()
        flash("Dodano wpis.")
        return redirect(url_for("admin_entries"))

    ym = request.args.get("month")
    if not ym:
        today = date.today()
        ym = f"{today.year:04d}-{today.month:02d}"
    year, month = map(int, ym.split("-"))
    m_from = date(year, month, 1)
    m_to = (m_from.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    selected_uid = request.args.get("user_id", "all")
    q = Entry.query.join(User).join(Project).filter(
        and_(Entry.work_date >= m_from, Entry.work_date <= m_to)
    )
    if selected_uid != "all":
        q = q.filter(Entry.user_id == int(selected_uid))

    entries = q.order_by(Entry.work_date.desc(), Entry.id.desc()).all()
    users = _all_users_ordered()
    projects = Project.query.order_by(Project.name).all()

    tot = sum(e.minutes for e in entries)
    tot_ex = sum(e.minutes for e in entries if e.is_extra)
    tot_ot = sum(e.minutes for e in entries if e.is_overtime)

    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Dodaj godziny (admin)</h5>
  <form class="row g-2" method="post">
    <div class="col-md-3">
      <label class="form-label">Pracownik</label>
      <select class="form-select" name="user_id" required>
        {% for u in users %}<option value="{{ u.id }}">{{ u.name }} ({{ u.email }})</option>{% endfor %}
      </select>
    </div>
    <div class="col-md-3">
      <label class="form-label">Projekt</label>
      <select class="form-select" name="project_id" required>
        {% for p in projects %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}
      </select>
    </div>
    <div class="col-md-2">
      <label class="form-label">Data</label>
      <input class="form-control" type="date" name="work_date" value="{{ date.today().isoformat() }}" required>
    </div>
    <div class="col-md-2">
      <label class="form-label">Czas (HH:MM)</label>
      <input class="form-control" type="text" name="hhmm" placeholder="np. 1:30" value="1:00" required>
    </div>
    <div class="col-md-2 d-flex align-items-end gap-3">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_extra" id="aextra">
        <label class="form-check-label" for="aextra">Extra</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_overtime" id="aot">
        <label class="form-check-label" for="aot">Nadgodziny</label>
      </div>
    </div>
    <div class="col-12">
      <label class="form-label">Notatka</label>
      <input class="form-control" type="text" name="note" placeholder="opcjonalnie">
    </div>
    <div class="col-12">
      <button class="btn btn-primary">Zapisz</button>
    </div>
  </form>
</div>

<div class="card p-3 mt-3">
  <form class="row g-2 align-items-end" method="get">
    <div class="col-md-3">
      <label class="form-label">Miesiąc</label>
      <input class="form-control" type="month" name="month" value="{{ ym }}">
    </div>
    <div class="col-md-5">
      <label class="form-label">Pracownik</label>
      <select class="form-select" name="user_id">
        <option value="all" {% if selected_uid == 'all' %}selected{% endif %}>Wszyscy</option>
        {% for u in users %}
          <option value="{{ u.id }}" {% if selected_uid|int == u.id %}selected{% endif %}>{{ u.name }} ({{ u.email }})</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-md-2">
      <button class="btn btn-outline-primary w-100">Filtruj</button>
    </div>
  </form>

  <div class="table-responsive mt-3">
    <table class="table table-sm align-middle">
      <thead>
        <tr><th>Data</th><th>Pracownik</th><th>Projekt</th><th>Notatka</th><th>Godziny</th><th>Extra</th><th>OT</th><th></th></tr>
      </thead>
      <tbody>
        {% for e in entries %}
        <tr>
          <td>{{ e.work_date.isoformat() }}</td>
          <td>{{ e.user.name }}</td>
          <td>{{ e.project.name }}</td>
          <td>{{ e.note or '' }}</td>
          <td>{{ fmt(e.minutes) }}</td>
          <td>{% if e.is_extra %}✔{% else %}-{% endif %}</td>
          <td>{% if e.is_overtime %}✔{% else %}-{% endif %}</td>
          <td class="text-nowrap">
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_entry_edit', entry_id=e.id) }}">Edytuj</a>
            <form class="d-inline" method="post" action="{{ url_for('admin_entry_delete', entry_id=e.id) }}" onsubmit="return confirm('Usunąć wpis?')">
              <button class="btn btn-sm btn-outline-danger">Usuń</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="mt-2">
    <span class="me-3">Razem: <strong>{{ fmt(tot) }}</strong></span>
    <span class="me-3">Extra: <strong>{{ fmt(tot_ex) }}</strong></span>
    <span class="me-3">Nadgodziny: <strong>{{ fmt(tot_ot) }}</strong></span>
  </div>
</div>
""", users=users, projects=projects, entries=entries, fmt=fmt_hhmm,
       ym=ym, selected_uid=selected_uid, tot=tot, tot_ex=tot_ex, tot_ot=tot_ot, date=date)
    return layout("Godziny (admin)", body)

@app.route("/admin/entries/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def admin_entry_edit(entry_id):
    require_admin()
    e = Entry.query.get_or_404(entry_id)
    users = _all_users_ordered()
    projects = Project.query.order_by(Project.name).all()

    if request.method == "POST":
        e.user_id = int(request.form.get("user_id"))
        e.project_id = int(request.form.get("project_id"))
        e.work_date = datetime.strptime(request.form.get("work_date"), "%Y-%m-%d").date()
        e.minutes = parse_hhmm(request.form.get("hhmm", "0"))
        e.is_extra = bool(request.form.get("is_extra"))
        e.is_overtime = bool(request.form.get("is_overtime"))
        e.note = request.form.get("note") or ""
        db.session.commit()
        flash("Zapisano zmiany.")
        return redirect(url_for("admin_entries"))

    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Edytuj wpis</h5>
  <form class="row g-2" method="post">
    <div class="col-md-3">
      <label class="form-label">Pracownik</label>
      <select class="form-select" name="user_id" required>
        {% for u in users %}<option value="{{ u.id }}" {% if u.id == e.user_id %}selected{% endif %}>{{ u.name }}</option>{% endfor %}
      </select>
    </div>
    <div class="col-md-3">
      <label class="form-label">Projekt</label>
      <select class="form-select" name="project_id" required>
        {% for p in projects %}<option value="{{ p.id }}" {% if p.id == e.project_id %}selected{% endif %}>{{ p.name }}</option>{% endfor %}
      </select>
    </div>
    <div class="col-md-2">
      <label class="form-label">Data</label>
      <input class="form-control" type="date" name="work_date" value="{{ e.work_date.isoformat() }}" required>
    </div>
    <div class="col-md-2">
      <label class="form-label">Czas (HH:MM)</label>
      <input class="form-control" type="text" name="hhmm" value="{{ fmt(e.minutes) }}" required>
    </div>
    <div class="col-md-2 d-flex align-items-end gap-3">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_extra" id="eextra" {% if e.is_extra %}checked{% endif %}>
        <label class="form-check-label" for="eextra">Extra</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="is_overtime" id="eot" {% if e.is_overtime %}checked{% endif %}>
        <label class="form-check-label" for="eot">Nadgodziny</label>
      </div>
    </div>
    <div class="col-12">
      <label class="form-label">Notatka</label>
      <input class="form-control" type="text" name="note" value="{{ e.note or '' }}">
    </div>
    <div class="col-12">
      <button class="btn btn-primary">Zapisz</button>
      <a class="btn btn-outline-secondary" href="{{ url_for('admin_entries') }}">Anuluj</a>
    </div>
  </form>
</div>
""", e=e, users=users, projects=projects, fmt=fmt_hhmm)
    return layout("Edytuj wpis", body)

@app.route("/admin/entries/<int:entry_id>/delete", methods=["POST"])
@login_required
def admin_entry_delete(entry_id):
    require_admin()
    e = Entry.query.get_or_404(entry_id)
    db.session.delete(e)
    db.session.commit()
    flash("Usunięto wpis.")
    return redirect(url_for("admin_entries"))


# --- Backup / Restore ---
def _make_zip_bytes(path)->bytes:
    ensure_db_file()
    if not os.path.exists(path):
        open(path, "a").close()
        ensure_db_file()
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(path, arcname="app.db")
    mem.seek(0)
    return mem.read()

def _replace_db_from_zipfileobj(fileobj):
    """Podmienia plik bazy danymi z archiwum ZIP (app.db w środku).

    1. Zamyka aktualne połączenia SQLAlchemy.
    2. Nadpisuje fizyczny plik DB_FILE zawartością app.db z backupu.
    3. Woła ensure_db_file(), aby upewnić się, że struktura tabel istnieje.
    """
    try:
        fileobj.seek(0)
    except Exception:
        pass

    # Ścieżka do aktualnej bazy
    target_path = DB_FILE
    target_dir = os.path.dirname(target_path) or "."
    os.makedirs(target_dir, exist_ok=True)

    # Zamykanie połączeń z bazą
    try:
        db.session.remove()
    except Exception:
        pass
    try:
        db.engine.dispose()
    except Exception:
        pass

    # Nadpisanie pliku bazy danymi z kopii zapasowej
    with zipfile.ZipFile(fileobj, "r") as z:
        if "app.db" not in z.namelist():
            raise RuntimeError("Brak pliku 'app.db' w archiwum.")
        with z.open("app.db") as src, open(target_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)

    # Odtworzenie struktury (jeśli trzeba), bez kasowania danych
    ensure_db_file()

@app.route("/admin/backup", methods=["GET"])
@login_required
def admin_backup():
    require_admin()
    base = os.path.dirname(DB_FILE)
    bdir = os.path.join(base, "backups") if base else "backups"
    os.makedirs(bdir, exist_ok=True)
    files = sorted([f for f in os.listdir(bdir) if f.endswith(".zip")])

    # Proste statystyki bieżącej bazy
    db_path = DB_FILE
    users = projects = entries = None
    try:
        users = User.query.count()
        projects = Project.query.count()
        entries = Entry.query.count()
    except Exception:
        pass

    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Kopie zapasowe</h5>
  <p class="small text-muted">
    Baza danych: <code>{{ db_path }}</code><br>
    Użytkownicy: {{ users if users is not none else "?" }},
    Projekty: {{ projects if projects is not none else "?" }},
    Wpisy: {{ entries if entries is not none else "?" }}
  </p>
  <form class="d-inline" method="post" action="{{ url_for('admin_backup_create') }}">
    <button class="btn btn-primary">Utwórz i pobierz kopię teraz</button>
  </form>
  <form class="d-inline ms-2" method="post" action="{{ url_for('admin_backup_create_save') }}">
    <button class="btn btn-outline-primary">Zapisz kopię na dysku serwera</button>
  </form>
  <form class="d-inline ms-2" method="post" action="{{ url_for('admin_backup_email') }}">
    <button class="btn btn-outline-success">Wyślij kopię zapasową na e-mail</button>
  </form>
  <hr class="my-3">
  <h6>Przywracanie z pliku (.zip)</h6>
  <form method="post" action="{{ url_for('admin_backup_restore') }}" enctype="multipart/form-data" onsubmit="return confirm('Zastąpić bieżącą bazę?')">
    <input class="form-control mb-2" type="file" name="file" accept=".zip" required>
    <button class="btn btn-danger">Przywróć</button>
  </form>
  <hr class="my-3">
  <h6>Pliki na dysku serwera</h6>
  {% if files %}
    <ul class="list-group">
      {% for f in files %}
        <li class="list-group-item d-flex justify-content-between align-items-center">
          <span>{{ f }}</span>
          <span>
            <a class="btn btn-sm btn-outline-success" href="{{ url_for('admin_backup_download', fname=f) }}">Pobierz</a>
            <a class="btn btn-sm btn-outline-danger" href="{{ url_for('admin_backup_restore_saved', fname=f) }}" onclick="return confirm('Przywrócić z tej kopii?')">Przywróć</a>
          </span>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <div class="text-muted">Brak zapisanych kopii.</div>
  {% endif %}
</div>
""", files=files, db_path=db_path, users=users, projects=projects, entries=entries)
    return layout("Kopie zapasowe", body)

@app.route("/admin/backup/create", methods=["POST"])
@login_required
def admin_backup_create():
    require_admin()
    data = _make_zip_bytes(DB_FILE)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"app_backup_{ts}.zip", mimetype="application/zip")

@app.route("/admin/backup/create_save", methods=["POST"])
@login_required
def admin_backup_create_save():
    require_admin()
    base = os.path.dirname(DB_FILE)
    bdir = os.path.join(base, "backups") if base else "backups"
    os.makedirs(bdir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(bdir, f"app_backup_{ts}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if not os.path.exists(DB_FILE):
            open(DB_FILE, "a").close()
            ensure_db_file()
        z.write(DB_FILE, arcname="app.db")
    flash(f"Zapisano: {os.path.basename(zip_path)}")
    return redirect(url_for("admin_backup"))



@app.route("/admin/backup/email", methods=["POST"])
@login_required
def admin_backup_email():
    require_admin()
    # Konfiguracja SMTP z zmiennych środowiskowych
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    backup_to = os.environ.get("BACKUP_EMAIL_TO")
    backup_from = os.environ.get("BACKUP_EMAIL_FROM") or smtp_user

    if not (smtp_host and smtp_port and smtp_user and smtp_password and backup_to):
        flash("Brak konfiguracji SMTP lub adresu docelowego BACKUP_EMAIL_TO.", "danger")
        return redirect(url_for("admin_backup"))

    # Przygotowanie danych kopii zapasowej w pamięci
    data = _make_zip_bytes(DB_FILE)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"app_backup_{ts}.zip"

    msg = EmailMessage()
    msg["Subject"] = f"Kopia zapasowa EKKO NOR – {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
    msg["From"] = backup_from
    msg["To"] = backup_to
    msg.set_content(
        "Kopia zapasowa bazy danych aplikacji EKKO NOR.\n"
        "Ta wiadomość została wygenerowana automatycznie przez system."
    )
    msg.add_attachment(data, maintype="application", subtype="zip", filename=fname)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        flash(f"Wysłano kopię zapasową na adres: {backup_to}", "success")
    except Exception as e:
        flash(f"Nie udało się wysłać kopii zapasowej: {e}", "danger")

    return redirect(url_for("admin_backup"))

@app.route("/admin/backup/download/<path:fname>")
@login_required
def admin_backup_download(fname):
    require_admin()
    base = os.path.dirname(DB_FILE)
    bdir = os.path.join(base, "backups") if base else "backups"
    path = os.path.join(bdir, secure_filename(fname))
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path), mimetype="application/zip")

@app.route("/admin/backup/restore", methods=["POST"])
@login_required
def admin_backup_restore():
    require_admin()
    f = request.files.get("file")
    if not f:
        flash("Nie wybrano pliku.")
        return redirect(url_for("admin_backup"))
    try:
        mem = io.BytesIO(f.read())
        _replace_db_from_zipfileobj(mem)
        # Statystyki po przywróceniu – żeby było widać, że dane są
        users = User.query.count()
        projects = Project.query.count()
        entries = Entry.query.count()
        flash(f"Przywrócono bazę z załączonego pliku. Użytkownicy: {users}, Projekty: {projects}, Wpisy: {entries}")
    except Exception as e:
        flash(f"Błąd przywracania: {e}")
    return redirect(url_for("admin_backup"))

@app.route("/admin/backup/restore_saved/<path:fname>")
@login_required
def admin_backup_restore_saved(fname):
    require_admin()
    base = os.path.dirname(DB_FILE)
    bdir = os.path.join(base, "backups") if base else "backups"
    path = os.path.join(bdir, secure_filename(fname))
    if not os.path.exists(path):
        abort(404)
    try:
        with open(path, "rb") as fp:
            _replace_db_from_zipfileobj(fp)
        # Statystyki po przywróceniu – żeby było widać, że dane są
        users = User.query.count()
        projects = Project.query.count()
        entries = Entry.query.count()
        flash(f"Przywrócono bazę z {fname}. Użytkownicy: {users}, Projekty: {projects}, Wpisy: {entries}")
    except Exception as e:
        flash(f"Błąd przywracania: {e}")
    return redirect(url_for("admin_backup"))


# --- Reports (with Excel export) ---


@app.route("/admin/reports", methods=["GET"])
@login_required

def admin_reports():
    require_admin()
    d_from = request.args.get("from")
    d_to = request.args.get("to")
    user_id = request.args.get("user_id")
    project_id = request.args.get("project_id")

    q = Entry.query.join(User).join(Project)
    if d_from:
        q = q.filter(Entry.work_date >= d_from)
    if d_to:
        q = q.filter(Entry.work_date <= d_to)
    if user_id and user_id != "all":
        q = q.filter(Entry.user_id == int(user_id))
    if project_id and project_id != "all":
        q = q.filter(Entry.project_id == int(project_id))

    rows = q.order_by(Entry.work_date.asc(), Entry.id.asc()).all()
    total_minutes = sum(e.minutes for e in rows)
    users = User.query.order_by(User.name).all()
    projects = Project.query.order_by(Project.name).all()

    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Raport</h5>

  <form class="row g-2 mb-3" method="get">
    <div class="col-md-3">
      <label class="form-label">Od</label>
      <input class="form-control" type="date" name="from" value="{{ request.args.get('from','') }}">
    </div>
    <div class="col-md-3">
      <label class="form-label">Do</label>
      <input class="form-control" type="date" name="to" value="{{ request.args.get('to','') }}">
    </div>
    <div class="col-md-3">
      <label class="form-label">Pracownik</label>
      <select class="form-select" name="user_id">
        <option value="all">Wszyscy</option>
        {% for u in users %}
          <option value="{{ u.id }}" {% if request.args.get('user_id')|int == u.id %}selected{% endif %}>{{ u.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-md-3">
      <label class="form-label">Projekt</label>
      <select class="form-select" name="project_id">
        <option value="all">Wszystkie</option>
        {% for p in projects %}
          <option value="{{ p.id }}" {% if request.args.get('project_id')|int == p.id %}selected{% endif %}>{{ p.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-12 d-flex gap-2">
      <button class="btn btn-primary">Pokaż</button>
    </div>
  </form>

  <p class="small text-muted">Łącznie rekordów: {{ rows|length }}</p>

  {% if rows %}
    <div class="mb-2 d-flex gap-2">
      <a class="btn btn-outline-success btn-sm"
         href="{{ url_for('admin_reports_export') }}?from={{ request.args.get('from','') }}&to={{ request.args.get('to','') }}&user_id={{ request.args.get('user_id','all') }}&project_id={{ request.args.get('project_id','all') }}">
        Eksport prosty (Excel)
      </a>
      <a class="btn btn-outline-primary btn-sm"
         href="{{ url_for('admin_reports_payroll') }}?from={{ request.args.get('from','') }}&to={{ request.args.get('to','') }}&user_id={{ request.args.get('user_id','all') }}&project_id={{ request.args.get('project_id','all') }}">
        Lista płac (Excel)
      </a>
    </div>
    <div class="table-responsive">
      <table class="table table-sm table-striped align-middle">
        <thead>
          <tr>
            <th>Data</th>
            <th>Pracownik</th>
            <th>Projekt</th>
            <th>Godziny</th>
            <th>Extra</th>
            <th>Nadgodziny</th>
            <th>Notatka</th>
          </tr>
        </thead>
        <tbody>
        {% for entry in rows %}
          <tr>
            <td>{{ entry.work_date }}</td>
            <td>{{ entry.user.name }}</td>
            <td>{{ entry.project.name }}</td>
            <td>{{ fmt(entry.minutes) }}</td>
            <td>{% if entry.is_extra %}tak{% else %}-{% endif %}</td>
            <td>{% if entry.is_overtime %}tak{% else %}-{% endif %}</td>
            <td>{{ entry.note }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    <div class="mt-2 fw-bold">
      Suma godzin: {{ fmt(total_minutes) }}
    </div>
  {% else %}
    <div class="text-muted">Brak wpisów.</div>
  {% endif %}
</div>
    """, rows=rows, users=users, projects=projects, fmt=fmt_hhmm, total_minutes=total_minutes)
    return layout("Raport", body)

@app.route("/admin/reports/export", methods=["GET"])
@login_required
def admin_reports_export():
    require_admin()
    try:
        from openpyxl import Workbook
    except Exception:
        abort(500, "Brak pakietu openpyxl (sprawdź requirements.txt)")

    d_from = request.args.get("from")
    d_to = request.args.get("to")
    user_id = request.args.get("user_id", "all")
    project_id = request.args.get("project_id", "all")
    if not d_from or not d_to:
        abort(400)

    d_from_dt = datetime.strptime(d_from, "%Y-%m-%d").date()
    d_to_dt = datetime.strptime(d_to, "%Y-%m-%d").date()
    q = Entry.query.join(User).join(Project).filter(
        Entry.work_date >= d_from_dt,
        Entry.work_date <= d_to_dt
    )
    if user_id != "all":
        q = q.filter(Entry.user_id == int(user_id))
    if project_id != "all":
        q = q.filter(Entry.project_id == int(project_id))
    rows = q.order_by(Entry.work_date.asc(), Entry.id.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Raport"
    ws.append(["Data", "Pracownik", "Projekt", "Godziny (HH:MM)", "Extra", "Nadgodziny", "Notatka"])
    for it in rows:
        ws.append([
            it.work_date.isoformat(),
            it.user.name,
            it.project.name,
            fmt_hhmm(it.minutes),
            "TAK" if it.is_extra else "",
            "TAK" if it.is_overtime else "",
            it.note or ""
        ])

    # podsumowanie
    total_min = sum(r.minutes for r in rows)
    ws.append([])
    ws.append(["Razem", "", "", fmt_hhmm(total_min), "", "", ""])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"raport_{d_from}_{d_to}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
@app.route("/admin/reports/payroll", methods=["GET"])
@login_required
def admin_reports_payroll():
    require_admin()
    try:
        from openpyxl import Workbook
    except Exception:
        abort(500, "Brak pakietu openpyxl (sprawdź requirements.txt)")

    d_from = request.args.get("from")
    d_to = request.args.get("to")
    user_id = request.args.get("user_id", "all")
    project_id = request.args.get("project_id", "all")
    if not d_from or not d_to:
        abort(400)

    d_from_dt = datetime.strptime(d_from, "%Y-%m-%d").date()
    d_to_dt = datetime.strptime(d_to, "%Y-%m-%d").date()

    q = Entry.query.join(User).join(Project).filter(
        Entry.work_date >= d_from_dt,
        Entry.work_date <= d_to_dt
    )
    if user_id != "all":
        q = q.filter(Entry.user_id == int(user_id))
    if project_id != "all":
        q = q.filter(Entry.project_id == int(project_id))

    rows = q.order_by(User.name.asc(), Entry.work_date.asc(), Entry.id.asc()).all()

    from collections import defaultdict
    per_user = defaultdict(list)
    for e in rows:
        per_user[e.user].append(e)

    wb = Workbook()
    # usuń domyślny arkusz, jeśli istnieje
    default_ws = wb.active
    wb.remove(default_ws)

    def sheet_title(user):
        base = user.name or f"Uzytkownik_{user.id}"
        for ch in '[]:*?/\\':
            base = base.replace(ch, "_")
        if len(base) > 25:
            base = base[:25]
        return base

    for user, entries in per_user.items():
        ws = wb.create_sheet(title=sheet_title(user))
        ws.append([f"Lista płac – {user.name}"])
        ws.append([f"Okres: {d_from_dt.isoformat()} – {d_to_dt.isoformat()}"])
        ws.append([])
        ws.append(["Data", "Projekt", "Godziny (HH:MM)", "Extra", "Nadgodziny", "Notatka"])

        total_minutes = 0
        extra_minutes = 0
        overtime_minutes = 0

        for e in entries:
            ws.append([
                e.work_date.isoformat(),
                e.project.name,
                fmt_hhmm(e.minutes),
                "TAK" if e.is_extra else "",
                "TAK" if e.is_overtime else "",
                e.note or "",
            ])
            total_minutes += e.minutes
            if e.is_extra:
                extra_minutes += e.minutes
            if e.is_overtime:
                overtime_minutes += e.minutes

        ws.append([])
        ws.append(["Suma godzin", "", fmt_hhmm(total_minutes), "", "", ""])
        ws.append(["Suma extra", "", fmt_hhmm(extra_minutes), "", "", ""])
        ws.append(["Suma nadgodzin", "", fmt_hhmm(overtime_minutes), "", "", ""])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"lista_plac_{d_from_dt}_{d_to_dt}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
# --- User: podsumowanie godzin (bieżący i poprzedni miesiąc) ---
@app.route("/my-summary")
@login_required
def user_summary():
    today = date.today()
    # bieżący miesiąc
    cur_first, cur_last = month_bounds(today)
    # poprzedni miesiąc – weź dzień przed pierwszym dniem bieżącego
    prev_ref = cur_first - timedelta(days=1)
    prev_first, prev_last = month_bounds(prev_ref)

    cur_entries = (
        Entry.query
        .filter(
            Entry.user_id == current_user.id,
            Entry.work_date >= cur_first,
            Entry.work_date <= cur_last,
        )
        .order_by(Entry.work_date.asc(), Entry.id.asc())
        .all()
    )
    prev_entries = (
        Entry.query
        .filter(
            Entry.user_id == current_user.id,
            Entry.work_date >= prev_first,
            Entry.work_date <= prev_last,
        )
        .order_by(Entry.work_date.asc(), Entry.id.asc())
        .all()
    )

    cur_total = sum((e.minutes or 0) for e in cur_entries)
    prev_total = sum((e.minutes or 0) for e in prev_entries)

    cur_label = cur_first.strftime("%Y-%m")
    prev_label = prev_first.strftime("%Y-%m")

    body = render_template_string("""
<div class="row">
  <div class="col-md-12">
    <div class="card p-3">
      <h5 class="mb-3">Moje godziny – {{ current_user.name }}</h5>
      <p class="text-muted small mb-3">
        Zestawienie czasu pracy za bieżący ({{ cur_label }}) i poprzedni ({{ prev_label }}) miesiąc.
      </p>
      <div class="row">
        <div class="col-md-6">
          <h6>Aktualny miesiąc ({{ cur_label }})</h6>
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Data</th>
                <th>Projekt</th>
                <th>Notatka</th>
                <th>Czas</th>
                <th>Extra</th>
                <th>OT</th>
              </tr>
            </thead>
            <tbody>
              {% for e in cur_entries %}
              <tr>
                <td>{{ e.work_date.isoformat() }}</td>
                <td>{{ e.project.name }}</td>
                <td>{{ e.note or '' }}</td>
                <td>{{ fmt(e.minutes) }}</td>
                <td>{% if e.is_extra %}✔{% else %}-{% endif %}</td>
                <td>{% if e.is_overtime %}✔{% else %}-{% endif %}</td>
              </tr>
              {% else %}
              <tr><td colspan="6" class="text-muted">Brak wpisów w tym miesiącu.</td></tr>
              {% endfor %}
            </tbody>
          </table>
          <div class="mt-2 fw-bold">Suma: {{ fmt(cur_total) }}</div>
        </div>
        <div class="col-md-6">
          <h6>Poprzedni miesiąc ({{ prev_label }})</h6>
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Data</th>
                <th>Projekt</th>
                <th>Notatka</th>
                <th>Czas</th>
                <th>Extra</th>
                <th>OT</th>
              </tr>
            </thead>
            <tbody>
              {% for e in prev_entries %}
              <tr>
                <td>{{ e.work_date.isoformat() }}</td>
                <td>{{ e.project.name }}</td>
                <td>{{ e.note or '' }}</td>
                <td>{{ fmt(e.minutes) }}</td>
                <td>{% if e.is_extra %}✔{% else %}-{% endif %}</td>
                <td>{% if e.is_overtime %}✔{% else %}-{% endif %}</td>
              </tr>
              {% else %}
              <tr><td colspan="6" class="text-muted">Brak wpisów w poprzednim miesiącu.</td></tr>
              {% endfor %}
            </tbody>
          </table>
          <div class="mt-2 fw-bold">Suma: {{ fmt(prev_total) }}</div>
        </div>
      </div>
    </div>
  </div>
</div>
""", cur_entries=cur_entries, prev_entries=prev_entries, fmt=fmt_hhmm,
       cur_total=cur_total, prev_total=prev_total,
       cur_label=cur_label, prev_label=prev_label, date=date)
    return layout("Moje godziny", body)


# --- User: koszty ---
@app.route("/costs", methods=["GET", "POST"])
@login_required
def user_costs():
    if request.method == "POST":
        cost_date_str = request.form.get("cost_date")
        amount = (request.form.get("amount") or "").strip()
        description = request.form.get("description") or ""

        try:
            cost_date = datetime.strptime(cost_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            flash("Nieprawidłowa data kosztu.")
            return redirect(url_for("user_costs"))

        if not amount:
            flash("Podaj kwotę kosztu.")
            return redirect(url_for("user_costs"))

        db.session.add(Cost(
            user_id=current_user.id,
            cost_date=cost_date,
            amount=amount,
            description=description,
        ))
        db.session.commit()
        flash("Dodano koszt.")
        return redirect(url_for("user_costs"))

    today = date.today()
    cur_first, cur_last = month_bounds(today)
    prev_ref = cur_first - timedelta(days=1)
    prev_first, prev_last = month_bounds(prev_ref)

    current_costs = (
        Cost.query
        .filter(
            Cost.user_id == current_user.id,
            Cost.cost_date >= cur_first,
            Cost.cost_date <= cur_last,
        )
        .order_by(Cost.cost_date.asc(), Cost.id.asc())
        .all()
    )
    previous_costs = (
        Cost.query
        .filter(
            Cost.user_id == current_user.id,
            Cost.cost_date >= prev_first,
            Cost.cost_date <= prev_last,
        )
        .order_by(Cost.cost_date.asc(), Cost.id.asc())
        .all()
    )

    cur_label = cur_first.strftime("%Y-%m")
    prev_label = prev_first.strftime("%Y-%m")

    body = render_template_string("""
<div class="row">
  <div class="col-md-12">
    <div class="card p-3">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">Moje koszty – {{ current_user.name }}</h5>
        <div class="text-end">
          <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('user_costs_export_xlsx') }}">Eksport Excel</a>
          <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('user_costs_print') }}">Druk</a>
        </div>
      </div>
      <form class="row g-2 mb-3" method="post">
        <div class="col-md-3">
          <label class="form-label">Data kosztu</label>
          <input class="form-control" type="date" name="cost_date" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-3">
          <label class="form-label">Kwota</label>
          <input class="form-control" type="text" name="amount" placeholder="np. 1234,50" required>
        </div>
        <div class="col-md-4">
          <label class="form-label">Opis</label>
          <input class="form-control" type="text" name="description" placeholder="np. paliwo, narzędzia">
        </div>
        <div class="col-md-2 d-flex align-items-end">
          <button class="btn btn-primary w-100">Dodaj koszt</button>
        </div>
      </form>

      <div class="row">
        <div class="col-md-6">
          <h6>Aktualny miesiąc ({{ cur_label }})</h6>
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Data</th>
                <th>Kwota</th>
                <th>Opis</th>
              </tr>
            </thead>
            <tbody>
              {% for c in current_costs %}
              <tr>
                <td>{{ c.cost_date.isoformat() }}</td>
                <td>{{ c.amount }}</td>
                <td>{{ c.description or '' }}</td>
              </tr>
              {% else %}
              <tr><td colspan="3" class="text-muted">Brak kosztów w tym miesiącu.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        <div class="col-md-6">
          <h6>Poprzedni miesiąc ({{ prev_label }})</h6>
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Data</th>
                <th>Kwota</th>
                <th>Opis</th>
              </tr>
            </thead>
            <tbody>
              {% for c in previous_costs %}
              <tr>
                <td>{{ c.cost_date.isoformat() }}</td>
                <td>{{ c.amount }}</td>
                <td>{{ c.description or '' }}</td>
              </tr>
              {% else %}
              <tr><td colspan="3" class="text-muted">Brak kosztów w poprzednim miesiącu.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      <p class="small text-muted mt-2 mb-0">
        Po zapisaniu kosztu nie możesz go już edytować – w razie potrzeby skontaktuj się z administratorem.
      </p>
    </div>
  </div>
</div>
""", current_costs=current_costs, previous_costs=previous_costs,
       cur_label=cur_label, prev_label=prev_label, date=date)
    return layout("Moje koszty", body)



@app.route("/costs/export.xlsx")
@login_required
def user_costs_export_xlsx():
    # eksport tylko swoich kosztów
    costs = (
        Cost.query.filter_by(user_id=current_user.id)
        .order_by(Cost.cost_date.desc(), Cost.id.desc())
        .all()
    )

    data_rows = []
    for c in costs:
        data_rows.append([
            c.cost_date.isoformat(),
            (c.amount or "").strip(),
            (c.description or "").strip(),
            (c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else ""),
        ])

    headers = ["Data", "Kwota", "Opis", "Utworzono"]
    bio_xlsx = _make_xlsx_bytes(headers, data_rows, sheet_name="Koszty")
    filename = f"koszty_{current_user.name}_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return send_file(
        bio_xlsx,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/costs/print")
@login_required
def user_costs_print():
    costs = (
        Cost.query.filter_by(user_id=current_user.id)
        .order_by(Cost.cost_date.desc(), Cost.id.desc())
        .all()
    )

    body = render_template_string(
        """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>Koszty – wydruk</title>
  <style>
    body { font-family: Arial, sans-serif; font-size: 12px; margin: 24px; }
    h2 { margin: 0 0 8px 0; }
    .meta { color: #555; margin-bottom: 14px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #999; padding: 6px 8px; vertical-align: top; }
    th { background: #f2f2f2; text-align: left; }
    .small { color:#666; font-size:11px; }
    @media print { .noprint { display:none; } }
  </style>
</head>
<body>
  <div class="noprint" style="margin-bottom:10px;">
    <button onclick="window.print()">Drukuj</button>
  </div>

  <h2>Koszty – {{ user.name }}</h2>
  <div class="meta">Wygenerowano: {{ now }}</div>

  <table>
    <thead>
      <tr>
        <th>Data</th>
        <th>Kwota</th>
        <th>Opis</th>
      </tr>
    </thead>
    <tbody>
      {% for c in costs %}
      <tr>
        <td>{{ c.cost_date.isoformat() }}</td>
        <td>{{ c.amount }}</td>
        <td>{{ c.description or '' }}</td>
      </tr>
      {% endfor %}
      {% if not costs %}
      <tr><td colspan="3" class="small">Brak danych.</td></tr>
      {% endif %}
    </tbody>
  </table>

  <script>window.onload = () => { window.print(); };</script>
</body>
</html>""",
        costs=costs,
        user=current_user,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return body




# --- Admin: koszty wszystkich użytkowników ---
@app.route("/admin/costs", methods=["GET", "POST"])
@login_required
def admin_costs():
    require_admin()

    if request.method == "POST":
        user_id = int(request.form.get("user_id"))
        cost_date_str = request.form.get("cost_date")
        amount = (request.form.get("amount") or "").strip()
        description = request.form.get("description") or ""

        try:
            cost_date = datetime.strptime(cost_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            flash("Nieprawidłowa data kosztu.")
            return redirect(url_for("admin_costs"))

        if not amount:
            flash("Podaj kwotę kosztu.")
            return redirect(url_for("admin_costs"))

        db.session.add(Cost(
            user_id=user_id,
            cost_date=cost_date,
            amount=amount,
            description=description,
        ))
        db.session.commit()
        flash("Dodano koszt.")
        return redirect(url_for("admin_costs"))

    users = _all_users_ordered()
    costs = (
        Cost.query
        .join(User)
        .order_by(Cost.cost_date.desc(), Cost.id.desc())
        .all()
    )

    body = render_template_string("""
<div class="row">
  <div class="col-md-12">
    <div class="card p-3">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0">Koszty – wszyscy użytkownicy</h5>
        <div class="text-end">
          <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('admin_costs_export_xlsx') }}">Eksport Excel</a>
          <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('admin_costs_print') }}">Druk</a>
        </div>
      </div>
      <form class="row g-2 mb-3" method="post">
        <div class="col-md-3">
          <label class="form-label">Pracownik</label>
          <select class="form-select" name="user_id" required>
            {% for u in users %}
              <option value="{{ u.id }}">{{ u.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label">Data kosztu</label>
          <input class="form-control" type="date" name="cost_date" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-3">
          <label class="form-label">Kwota</label>
          <input class="form-control" type="text" name="amount" placeholder="np. 1234,50" required>
        </div>
        <div class="col-md-3">
          <label class="form-label">Opis</label>
          <input class="form-control" type="text" name="description" placeholder="np. paliwo, narzędzia">
        </div>
        <div class="col-md-12 d-flex justify-content-end mt-2">
          <button class="btn btn-primary">Dodaj koszt</button>
        </div>
      </form>

      <div class="table-responsive">
        <table class="table table-sm align-middle">
          <thead>
            <tr>
              <th>Data</th>
              <th>Pracownik</th>
              <th>Kwota</th>
              <th>Opis</th>
              <th class="text-end">Akcje</th>
            </tr>
          </thead>
          <tbody>
            {% for c in costs %}
            <tr>
              <td>{{ c.cost_date.isoformat() }}</td>
              <td>{{ c.user.name }}</td>
              <td>{{ c.amount }}</td>
              <td>{{ c.description or '' }}</td>
              <td class="text-end">
                <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_cost_edit', cost_id=c.id) }}">Edytuj</a>
                <form class="d-inline" method="post" action="{{ url_for('admin_cost_delete', cost_id=c.id) }}" onsubmit="return confirm('Na pewno usunąć ten koszt?');">
                  <button class="btn btn-sm btn-outline-danger">Usuń</button>
                </form>
              </td>
            </tr>
            {% else %}
            <tr><td colspan="5" class="text-muted">Brak kosztów.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
""", users=users, costs=costs, date=date)
    return layout("Koszty (admin)", body)



@app.route("/admin/costs/export.xlsx")
@login_required
def admin_costs_export_xlsx():
    require_admin()

    costs = (
        Cost.query.join(User)
        .order_by(Cost.cost_date.desc(), Cost.id.desc())
        .all()
    )

    data_rows = []
    for c in costs:
        data_rows.append([
            c.user.name,
            c.cost_date.isoformat(),
            (c.amount or "").strip(),
            (c.description or "").strip(),
            (c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else ""),
        ])

    headers = ["Użytkownik", "Data", "Kwota", "Opis", "Utworzono"]
    bio_xlsx = _make_xlsx_bytes(headers, data_rows, sheet_name="Koszty")
    filename = f"koszty_admin_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return send_file(
        bio_xlsx,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/costs/print")
@login_required
def admin_costs_print():
    require_admin()

    costs = (
        Cost.query.join(User)
        .order_by(Cost.cost_date.desc(), Cost.id.desc())
        .all()
    )

    body = render_template_string(
        """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>Koszty – wydruk (admin)</title>
  <style>
    body { font-family: Arial, sans-serif; font-size: 12px; margin: 24px; }
    h2 { margin: 0 0 8px 0; }
    .meta { color: #555; margin-bottom: 14px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #999; padding: 6px 8px; vertical-align: top; }
    th { background: #f2f2f2; text-align: left; }
    .small { color:#666; font-size:11px; }
    @media print { .noprint { display:none; } }
  </style>
</head>
<body>
  <div class="noprint" style="margin-bottom:10px;">
    <button onclick="window.print()">Drukuj</button>
  </div>

  <h2>Koszty – zestawienie (admin)</h2>
  <div class="meta">Wygenerowano: {{ now }}</div>

  <table>
    <thead>
      <tr>
        <th>Użytkownik</th>
        <th>Data</th>
        <th>Kwota</th>
        <th>Opis</th>
      </tr>
    </thead>
    <tbody>
      {% for c in costs %}
      <tr>
        <td>{{ c.user.name }}</td>
        <td>{{ c.cost_date.isoformat() }}</td>
        <td>{{ c.amount }}</td>
        <td>{{ c.description or '' }}</td>
      </tr>
      {% endfor %}
      {% if not costs %}
      <tr><td colspan="4" class="small">Brak danych.</td></tr>
      {% endif %}
    </tbody>
  </table>

  <script>window.onload = () => { window.print(); };</script>
</body>
</html>""",
        costs=costs,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return body




@app.route("/admin/costs/<int:cost_id>/edit", methods=["GET", "POST"])
@login_required
def admin_cost_edit(cost_id):
    require_admin()
    cost = Cost.query.get_or_404(cost_id)
    users = _all_users_ordered()

    if request.method == "POST":
        cost.user_id = int(request.form.get("user_id"))
        cost_date_str = request.form.get("cost_date")
        cost.amount = (request.form.get("amount") or "").strip()
        cost.description = request.form.get("description") or ""

        try:
            cost.cost_date = datetime.strptime(cost_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            flash("Nieprawidłowa data kosztu.")
            return redirect(url_for("admin_cost_edit", cost_id=cost.id))

        if not cost.amount:
            flash("Podaj kwotę kosztu.")
            return redirect(url_for("admin_cost_edit", cost_id=cost.id))

        db.session.commit()
        flash("Zapisano zmiany.")
        return redirect(url_for("admin_costs"))

    body = render_template_string("""
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card p-3">
      <h5 class="mb-3">Edytuj koszt</h5>
      <form class="row g-2" method="post">
        <div class="col-md-6">
          <label class="form-label">Pracownik</label>
          <select class="form-select" name="user_id" required>
            {% for u in users %}
              <option value="{{ u.id }}" {% if u.id == cost.user_id %}selected{% endif %}>{{ u.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-6">
          <label class="form-label">Data kosztu</label>
          <input class="form-control" type="date" name="cost_date" value="{{ cost.cost_date.isoformat() }}" required>
        </div>
        <div class="col-md-6">
          <label class="form-label">Kwota</label>
          <input class="form-control" type="text" name="amount" value="{{ cost.amount }}" required>
        </div>
        <div class="col-md-12">
          <label class="form-label">Opis</label>
          <input class="form-control" type="text" name="description" value="{{ cost.description or '' }}">
        </div>
        <div class="col-md-12 d-flex justify-content-end">
          <button class="btn btn-primary">Zapisz</button>
        </div>
      </form>
    </div>
  </div>
</div>
""", users=users, cost=cost)
    return layout("Edytuj koszt", body)


@app.route("/admin/costs/<int:cost_id>/delete", methods=["POST"])
@login_required
def admin_cost_delete(cost_id):
    require_admin()
    cost = Cost.query.get_or_404(cost_id)
    db.session.delete(cost)
    db.session.commit()
    flash("Usunięto koszt.")
    return redirect(url_for("admin_costs"))


# --- Urlopy (Leave requests) ---
def _leave_status_pl(s: str) -> str:
    s = (s or "").upper()
    if s == "DRAFT":
        return "Szkic"
    if s == "SUBMITTED":
        return "Wysłane"
    if s == "APPROVED":
        return "Zaakceptowane"
    return s or "-"




def _make_xlsx_bytes(headers, rows, sheet_name="Dane"):
    """headers: list[str], rows: iterable[iterable]"""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31] if sheet_name else "Dane"

    # Header
    ws.append(list(headers))

    # Rows
    for r in rows:
        ws.append(list(r))

    # Basic formatting: bold header + autosize
    try:
        from openpyxl.styles import Font
        for cell in ws[1]:
            cell.font = Font(bold=True)
    except Exception:
        pass

    for col_idx in range(1, len(headers) + 1):
        max_len = 0
        for cell in ws[get_column_letter(col_idx)]:
            v = cell.value
            if v is None:
                continue
            s = str(v)
            if len(s) > max_len:
                max_len = len(s)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(10, max_len + 2), 60)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio

@app.route("/leaves", methods=["GET", "POST"])
@login_required
def leaves():
    # Użytkownik: tworzy szkic urlopu
    if not current_user.is_admin and request.method == "POST":
        df = request.form.get("date_from")
        dt = request.form.get("date_to")
        reason = request.form.get("reason") or ""

        try:
            date_from = datetime.strptime(df, "%Y-%m-%d").date()
            date_to = datetime.strptime(dt, "%Y-%m-%d").date()
        except Exception:
            flash("Nieprawidłowa data.")
            return redirect(url_for("leaves"))

        if date_to < date_from:
            flash("Data 'do' nie może być wcześniejsza niż 'od'.")
            return redirect(url_for("leaves"))

        lr = LeaveRequest(
            user_id=current_user.id,
            date_from=date_from,
            date_to=date_to,
            reason=reason,
            status="DRAFT",
        )
        db.session.add(lr)
        db.session.commit()
        flash("Dodano prośbę o urlop (szkic).")
        return redirect(url_for("leaves"))


    # Admin: dodaje urlop wybranemu użytkownikowi (od razu zaakceptowany)
    if current_user.is_admin and request.method == "POST" and request.form.get("action") == "admin_add":
        uid = request.form.get("user_id")
        df = request.form.get("date_from")
        dt = request.form.get("date_to")
        reason = request.form.get("reason") or ""

        try:
            user_id = int(uid)
            date_from = datetime.strptime(df, "%Y-%m-%d").date()
            date_to = datetime.strptime(dt, "%Y-%m-%d").date()
        except Exception:
            flash("Nieprawidłowe dane formularza.", "danger")
            return redirect(url_for("leaves"))

        if date_to < date_from:
            flash("Data 'Do' nie może być wcześniejsza niż 'Od'.", "danger")
            return redirect(url_for("leaves"))

        u = User.query.get(user_id)
        if not u:
            flash("Nie znaleziono użytkownika.", "danger")
            return redirect(url_for("leaves"))

        now = datetime.utcnow()
        lr = LeaveRequest(
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            reason=reason,
            status="APPROVED",
            submitted_at=now,
            decided_at=now,
            decided_by=current_user.id,
        )
        db.session.add(lr)
        db.session.commit()
        flash("Urlop został dodany i zaakceptowany.", "success")
        return redirect(url_for("leaves"))

    # Admin: lista wszystkich
    if current_user.is_admin:
        users = User.query.order_by(User.name.asc(), User.id.asc()).all()
        rows = (
            LeaveRequest.query
            .join(User, LeaveRequest.user_id == User.id)
            .order_by(LeaveRequest.created_at.desc(), LeaveRequest.id.desc())
            .all()
        )

        body = render_template_string("""
<div class="card p-3">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h5 class="mb-0">Urlopy – wszystkie prośby</h5>
    <div class="text-end">
      <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('admin_leaves_export_xlsx') }}">Eksport Excel</a>
      <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('admin_leaves_print') }}">Druk</a>
    </div>
  </div>
  <form method="post" class="row g-2 mb-3">
    <input type="hidden" name="action" value="admin_add">
    <div class="col-12 col-md-3">
      <select class="form-select form-select-sm" name="user_id" required>
        <option value="" disabled selected>Wybierz użytkownika</option>
        {% for u in users %}
          <option value="{{ u.id }}">{{ u.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-6 col-md-2">
      <input class="form-control form-control-sm" type="date" name="date_from" required>
    </div>
    <div class="col-6 col-md-2">
      <input class="form-control form-control-sm" type="date" name="date_to" required>
    </div>
    <div class="col-12 col-md-3">
      <input class="form-control form-control-sm" type="text" name="reason" placeholder="Uzasadnienie (opcjonalnie)">
    </div>
    <div class="col-12 col-md-2 text-md-end">
      <button class="btn btn-sm btn-primary" type="submit">Dodaj urlop</button>
    </div>
  </form>
  <div class="table-responsive">
    <table class="table table-sm align-middle">
      <thead>
        <tr>
          <th>Pracownik</th>
          <th>Od</th>
          <th>Do</th>
          <th>Status</th>
          <th>Uzasadnienie</th>
          <th class="text-end">Akcje</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rows %}
        <tr>
          <td>{{ r.user.name }}</td>
          <td>{{ r.date_from.isoformat() }}</td>
          <td>{{ r.date_to.isoformat() }}</td>
          <td>
            <span class="badge badge-soft">{{ status_pl(r.status) }}</span>
          </td>
          <td style="max-width:420px;">{{ (r.reason or '')[:250] }}{% if r.reason and r.reason|length > 250 %}...{% endif %}</td>
          <td class="text-end text-nowrap">
            {% if r.status != 'APPROVED' %}
              <form class="d-inline" method="post" action="{{ url_for('leave_approve', leave_id=r.id) }}" onsubmit="return confirm('Zaakceptować ten urlop?')">
                <button class="btn btn-sm btn-outline-success">Akceptuj</button>
              </form>
            {% endif %}
            <form class="d-inline" method="post" action="{{ url_for('leave_delete', leave_id=r.id) }}" onsubmit="return confirm('Usunąć tę prośbę?')">
              <button class="btn btn-sm btn-outline-danger">Usuń</button>
            </form>
          </td>
        </tr>
        {% else %}
          <tr><td colspan="6" class="text-muted">Brak próśb o urlop.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""", rows=rows, users=users, status_pl=_leave_status_pl)
        return layout("Urlopy (admin)", body)

    # User: lista swoich
    rows = (
        LeaveRequest.query
        .filter(LeaveRequest.user_id == current_user.id)
        .order_by(LeaveRequest.created_at.desc(), LeaveRequest.id.desc())
        .all()
    )

    body = render_template_string("""
<div class="row g-3">
  <div class="col-12">
    <div class="card p-3">
      <h5 class="mb-3">Zgłoś urlop</h5>
      <form class="row g-2" method="post">
        <div class="col-md-3">
          <label class="form-label">Od</label>
          <input class="form-control" type="date" name="date_from" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-3">
          <label class="form-label">Do</label>
          <input class="form-control" type="date" name="date_to" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-6">
          <label class="form-label">Uzasadnienie</label>
          <input class="form-control" type="text" name="reason" placeholder="np. wyjazd, sprawy rodzinne">
        </div>
        <div class="col-12">
          <button class="btn btn-primary">Dodaj (szkic)</button>
        </div>
      </form>
      <div class="small text-muted mt-2">
        Najpierw dodajesz szkic, potem przy prośbie klikasz „Wyślij do akceptacji”.
      </div>
    </div>
  </div>

  <div class="col-12">
    <div class="card p-3">
      <h5 class="mb-0">Moje urlopy</h5>
      <div class="table-responsive mt-3">
        <table class="table table-sm align-middle">
          <thead>
            <tr>
              <th>Od</th>
              <th>Do</th>
              <th>Status</th>
              <th>Uzasadnienie</th>
              <th class="text-end">Akcje</th>
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
            <tr>
              <td>{{ r.date_from.isoformat() }}</td>
              <td>{{ r.date_to.isoformat() }}</td>
              <td><span class="badge badge-soft">{{ status_pl(r.status) }}</span></td>
              <td style="max-width:520px;">{{ r.reason or '' }}</td>
              <td class="text-end text-nowrap">
                {% if r.status != 'APPROVED' %}
                  <a class="btn btn-sm btn-outline-primary" href="{{ url_for('leave_edit', leave_id=r.id) }}">Edytuj</a>
                  <form class="d-inline" method="post" action="{{ url_for('leave_delete', leave_id=r.id) }}" onsubmit="return confirm('Usunąć tę prośbę?')">
                    <button class="btn btn-sm btn-outline-danger">Usuń</button>
                  </form>
                  {% if r.status == 'DRAFT' %}
                    <form class="d-inline" method="post" action="{{ url_for('leave_submit', leave_id=r.id) }}" onsubmit="return confirm('Wysłać do akceptacji?')">
                      <button class="btn btn-sm btn-outline-success">Wyślij do akceptacji</button>
                    </form>
                  {% endif %}
                {% else %}
                  <span class="text-muted">Zaakceptowane – bez zmian</span>
                {% endif %}
              </td>
            </tr>
            {% else %}
              <tr><td colspan="5" class="text-muted">Brak próśb o urlop.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
""", rows=rows, status_pl=_leave_status_pl, date=date)
    return layout("Urlopy", body)



@app.route("/admin/leaves/export.xlsx")
@login_required
def admin_leaves_export_xlsx():
    require_admin()
    rows = (
        LeaveRequest.query.join(User)
        .order_by(LeaveRequest.created_at.desc())
        .all()
    )

    data_rows = []
    for r in rows:
        days = None
        try:
            days = (r.date_to - r.date_from).days + 1
        except Exception:
            pass
        data_rows.append([
            r.user.name,
            r.date_from.isoformat(),
            r.date_to.isoformat(),
            days,
            _leave_status_pl(r.status),
            (r.reason or "").strip(),
            (r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""),
            (r.submitted_at.strftime("%Y-%m-%d %H:%M") if getattr(r, "submitted_at", None) else ""),
            (r.decided_at.strftime("%Y-%m-%d %H:%M") if getattr(r, "decided_at", None) else ""),
        ])

    headers = ["Użytkownik", "Od", "Do", "Dni", "Status", "Uzasadnienie", "Utworzono", "Wysłano", "Zaakceptowano"]
    bio_xlsx = _make_xlsx_bytes(headers, data_rows, sheet_name="Urlopy")
    filename = f"urlopy_admin_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return send_file(
        bio_xlsx,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/leaves/print")
@login_required
def admin_leaves_print():
    require_admin()
    rows = (
        LeaveRequest.query.join(User)
        .order_by(LeaveRequest.created_at.desc())
        .all()
    )

    body = render_template_string(
        """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>Urlopy – wydruk</title>
  <style>
    body { font-family: Arial, sans-serif; font-size: 12px; margin: 24px; }
    h2 { margin: 0 0 8px 0; }
    .meta { color: #555; margin-bottom: 14px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #999; padding: 6px 8px; vertical-align: top; }
    th { background: #f2f2f2; text-align: left; }
    .small { color:#666; font-size:11px; }
    @media print { .noprint { display:none; } }
  </style>
</head>
<body>
  <div class="noprint" style="margin-bottom:10px;">
    <button onclick="window.print()">Drukuj</button>
  </div>

  <h2>Urlopy – zestawienie (admin)</h2>
  <div class="meta">Wygenerowano: {{ now }}</div>

  <table>
    <thead>
      <tr>
        <th>Użytkownik</th>
        <th>Od</th>
        <th>Do</th>
        <th>Dni</th>
        <th>Status</th>
        <th>Uzasadnienie</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ r.user.name }}</td>
        <td>{{ r.date_from.isoformat() }}</td>
        <td>{{ r.date_to.isoformat() }}</td>
        <td>
          {% set d = (r.date_to - r.date_from).days + 1 %}
          {{ d }}
        </td>
        <td>{{ status_pl(r.status) }}</td>
        <td>{{ r.reason or '' }}</td>
      </tr>
      {% endfor %}
      {% if not rows %}
      <tr><td colspan="6" class="small">Brak danych.</td></tr>
      {% endif %}
    </tbody>
  </table>

  <script>window.onload = () => { window.print(); };</script>
</body>
</html>""",
        rows=rows,
        status_pl=_leave_status_pl,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return body




@app.route("/leaves/<int:leave_id>/edit", methods=["GET", "POST"])
@login_required
def leave_edit(leave_id):
    lr = LeaveRequest.query.get_or_404(leave_id)
    if not (current_user.is_admin or lr.user_id == current_user.id):
        abort(403)

    if lr.status == "APPROVED":
        flash("Zaakceptowanej prośby nie można edytować.")
        return redirect(url_for("leaves"))

    if request.method == "POST":
        df = request.form.get("date_from")
        dt = request.form.get("date_to")
        reason = request.form.get("reason") or ""

        try:
            date_from = datetime.strptime(df, "%Y-%m-%d").date()
            date_to = datetime.strptime(dt, "%Y-%m-%d").date()
        except Exception:
            flash("Nieprawidłowa data.")
            return redirect(url_for("leave_edit", leave_id=leave_id))

        if date_to < date_from:
            flash("Data 'do' nie może być wcześniejsza niż 'od'.")
            return redirect(url_for("leave_edit", leave_id=leave_id))

        lr.date_from = date_from
        lr.date_to = date_to
        lr.reason = reason
        db.session.commit()
        flash("Zapisano zmiany.")
        return redirect(url_for("leaves"))

    body = render_template_string("""
<div class="row justify-content-center">
  <div class="col-md-7">
    <div class="card p-3">
      <h5 class="mb-3">Edytuj prośbę o urlop</h5>
      <form class="row g-2" method="post">
        <div class="col-md-4">
          <label class="form-label">Od</label>
          <input class="form-control" type="date" name="date_from" value="{{ lr.date_from.isoformat() }}" required>
        </div>
        <div class="col-md-4">
          <label class="form-label">Do</label>
          <input class="form-control" type="date" name="date_to" value="{{ lr.date_to.isoformat() }}" required>
        </div>
        <div class="col-md-12">
          <label class="form-label">Uzasadnienie</label>
          <input class="form-control" type="text" name="reason" value="{{ lr.reason or '' }}">
        </div>
        <div class="col-12 d-flex gap-2">
          <button class="btn btn-primary">Zapisz</button>
          <a class="btn btn-outline-secondary" href="{{ url_for('leaves') }}">Anuluj</a>
        </div>
      </form>
      {% if lr.status == 'SUBMITTED' %}
        <div class="small text-muted mt-2">Ta prośba jest już wysłana do akceptacji. Nadal możesz ją edytować/usunąć, dopóki nie zostanie zaakceptowana.</div>
      {% endif %}
    </div>
  </div>
</div>
""", lr=lr)
    return layout("Edytuj urlop", body)


@app.route("/leaves/<int:leave_id>/delete", methods=["POST"])
@login_required
def leave_delete(leave_id):
    lr = LeaveRequest.query.get_or_404(leave_id)

    if current_user.is_admin:
        db.session.delete(lr)
        db.session.commit()
        flash("Usunięto prośbę o urlop.")
        return redirect(url_for("leaves"))

    if lr.user_id != current_user.id:
        abort(403)

    if lr.status == "APPROVED":
        flash("Zaakceptowanej prośby nie można usunąć.")
        return redirect(url_for("leaves"))

    db.session.delete(lr)
    db.session.commit()
    flash("Usunięto prośbę o urlop.")
    return redirect(url_for("leaves"))


@app.route("/leaves/<int:leave_id>/submit", methods=["POST"])
@login_required
def leave_submit(leave_id):
    lr = LeaveRequest.query.get_or_404(leave_id)
    if lr.user_id != current_user.id and not current_user.is_admin:
        abort(403)

    if lr.status == "APPROVED":
        flash("Ta prośba jest już zaakceptowana.")
        return redirect(url_for("leaves"))

    if lr.status != "DRAFT":
        flash("Ta prośba jest już wysłana do akceptacji.")
        return redirect(url_for("leaves"))

    lr.status = "SUBMITTED"
    lr.submitted_at = datetime.utcnow()
    db.session.commit()
    flash("Wysłano do akceptacji.")
    return redirect(url_for("leaves"))


@app.route("/leaves/<int:leave_id>/approve", methods=["POST"])
@login_required
def leave_approve(leave_id):
    require_admin()
    lr = LeaveRequest.query.get_or_404(leave_id)

    if lr.status == "APPROVED":
        flash("Ta prośba jest już zaakceptowana.")
        return redirect(url_for("leaves"))

    lr.status = "APPROVED"
    lr.decided_at = datetime.utcnow()
    lr.decided_by = current_user.id
    db.session.commit()
    flash("Zaakceptowano prośbę o urlop.")
    return redirect(url_for("leaves"))




if __name__ == "__main__":
    ensure_db_file()
    app.run(debug=True, host="0.0.0.0")