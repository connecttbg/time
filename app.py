
import os
import io
import zipfile
import tempfile
from datetime import datetime, date, timedelta
from flask import Flask, request, redirect, url_for, send_file, abort, flash, render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import text as sql_text  # ensure DB file creation

# --- Flask & DB config ---
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.getenv("SECRET_KEY", "dev-key-change-me")

DB_FILE = "/var/data/app.db" if os.path.exists("/var/data") else "app.db"
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
      <span class="fw-semibold">EKKO NOR AS</span>
    </a>
    {% if current_user.is_authenticated %}
    <div class="ms-auto d-flex align-items-center gap-2">
      {% if current_user.is_admin %}
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_users') }}">Pracownicy</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_projects') }}">Projekty</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_reports') }}">Raport</a>
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_backup') }}">Kopie</a>
      {% endif %}
      <span class="badge badge-soft px-3 py-2">{{ current_user.name }}</span>
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
</body>
</html>
"""

def layout(title, body):
    return render_template_string(BASE, title=title, body=body, fmt=fmt_hhmm)


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


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# --- Dashboard ---
@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        work_date = request.form.get("work_date")
        project_id = int(request.form.get("project_id"))
        hhmm = request.form.get("hhmm", "0")
        minutes = parse_hhmm(hhmm)
        is_extra = bool(request.form.get("is_extra"))
        is_overtime = bool(request.form.get("is_overtime"))
        note = request.form.get("note") or ""

        e = Entry(
            user_id=current_user.id,
            project_id=project_id,
            work_date=datetime.strptime(work_date, "%Y-%m-%d").date(),
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


# --- Edit/Delete entries ---
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
        return redirect(url_for("dashboard" if not current_user.is_admin else "admin_reports"))

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
    return redirect(url_for("dashboard" if not current_user.is_admin else "admin_reports"))


# --- Admin: users ---
@app.route("/admin/users")
@login_required
def admin_users():
    require_admin()
    users = User.query.order_by(User.name).all()
    body = render_template_string("""
<div class="card p-3">
  <div class="d-flex justify-content-between align-items-center">
    <h5 class="mb-0">Pracownicy</h5>
    <a class="btn btn-primary" href="{{ url_for('admin_user_add') }}">Dodaj pracownika</a>
  </div>
  <div class="table-responsive mt-3">
    <table class="table table-sm align-middle">
      <thead><tr><th>Imię i nazwisko</th><th>E-mail</th><th>Rola</th><th>Aktywny</th><th class="text-end">Akcje</th></tr></thead>
      <tbody>
      {% for u in users %}
        <tr>
          <td>{{ u.name }}</td>
          <td>{{ u.email }}</td>
          <td>{% if u.is_admin %}Admin{% else %}Użytkownik{% endif %}</td>
          <td>{% if u.is_active %}✔{% else %}-{% endif %}</td>
          <td class="text-end">
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


@app.route("/admin/users/add", methods=["GET", "POST"])
@login_required
def admin_user_add():
    require_admin()
    if request.method == "POST":
        name = request.form.get("name").strip()
        email = request.form.get("email").strip().lower()
        pw = request.form.get("password").strip()
        is_admin = bool(request.form.get("is_admin"))
        is_active = bool(request.form.get("is_active"))
        u = User(name=name, email=email, is_admin=is_admin, is_active_u=is_active)
        u.set_password(pw or "changeme123")
        db.session.add(u)
        db.session.commit()
        flash("Dodano pracownika.")
        return redirect(url_for("admin_users"))

    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Dodaj pracownika</h5>
  <form class="row g-2" method="post">
    <div class="col-md-6">
      <label class="form-label">Imię i nazwisko</label>
      <input class="form-control" name="name" required>
    </div>
    <div class="col-md-6">
      <label class="form-label">E-mail</label>
      <input class="form-control" type="email" name="email" required>
    </div>
    <div class="col-md-6">
      <label class="form-label">Hasło (tymczasowe)</label>
      <input class="form-control" type="text" name="password" placeholder="np. Changeme123">
    </div>
    <div class="col-md-6 d-flex align-items-end gap-3">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" id="adm" name="is_admin">
        <label class="form-check-label" for="adm">Administrator</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="checkbox" id="act" name="is_active" checked>
        <label class="form-check-label" for="act">Aktywny</label>
      </div>
    </div>
    <div class="col-12">
      <button class="btn btn-primary">Zapisz</button>
    </div>
  </form>
</div>
""")
    return layout("Dodaj pracownika", body)


@app.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@login_required
def admin_user_edit(uid):
    require_admin()
    u = User.query.get_or_404(uid)

    if request.method == "POST":
        u.name = request.form.get("name").strip()
        new_email = request.form.get("email").strip().lower()
        if new_email != u.email and User.query.filter_by(email=new_email).first():
            flash("Ten e-mail jest już zajęty.")
            return redirect(url_for("admin_user_edit", uid=uid))
        u.email = new_email
        u.is_admin = bool(request.form.get("is_admin"))
        u.is_active_u = bool(request.form.get("is_active"))
        new_pw = request.form.get("password") or ""
        if new_pw:
            u.set_password(new_pw)
        db.session.commit()
        flash("Zaktualizowano dane pracownika.")
        return redirect(url_for("admin_users"))

    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Edytuj pracownika</h5>
  <form class="row g-2" method="post">
    <div class="col-md-6">
      <label class="form-label">Imię i nazwisko</label>
      <input class="form-control" name="name" value="{{ u.name }}" required>
    </div>
    <div class="col-md-6">
      <label class="form-label">E-mail</label>
      <input class="form-control" type="email" name="email" value="{{ u.email }}" required>
    </div>
    <div class="col-md-6">
      <label class="form-label">Nowe hasło (opcjonalnie)</label>
      <input class="form-control" type="text" name="password" placeholder="pozostaw puste, aby nie zmieniać">
    </div>
    <div class="col-md-6 d-flex align-items-end gap-3">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" id="adm" name="is_admin" {% if u.is_admin %}checked{% endif %}>
        <label class="form-check-label" for="adm">Administrator</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="checkbox" id="act" name="is_active" {% if u.is_active %}checked{% endif %}>
        <label class="form-check-label" for="act">Aktywny</label>
      </div>
    </div>
    <div class="col-12">
      <button class="btn btn-primary">Zapisz</button>
      <a class="btn btn-outline-secondary" href="{{ url_for('admin_users') }}">Wróć</a>
    </div>
  </form>
</div>
""", u=u)
    return layout("Edytuj pracownika", body)


# --- Admin: projects ---
@app.route("/admin/projects", methods=["GET", "POST"])
@login_required
def admin_projects():
    require_admin()
    if request.method == "POST":
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
      <input class="form-control me-2" name="name" placeholder="Nazwa projektu" required>
      <button class="btn btn-primary">Dodaj</button>
    </form>
  </div>
  <div class="table-responsive mt-3">
    <table class="table table-sm align-middle">
      <thead><tr><th>Nazwa</th><th>Aktywny</th><th class="text-end">Akcje</th></tr></thead>
      <tbody>
      {% for p in projs %}
        <tr>
          <td>
            <form class="d-flex" method="post" action="{{ url_for('admin_project_update', pid=p.id) }}">
              <input class="form-control form-control-sm me-2" name="name" value="{{ p.name }}">
              <button class="btn btn-sm btn-outline-primary">Zapisz</button>
            </form>
          </td>
          <td>{% if p.is_active %}✔{% else %}-{% endif %}</td>
          <td class="text-end">
            <form class="d-inline" method="post" action="{{ url_for('admin_project_toggle', pid=p.id) }}">
              <button class="btn btn-sm btn-outline-secondary">{% if p.is_active %}Dezaktywuj{% else %}Aktywuj{% endif %}</button>
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


@app.route("/admin/projects/<int:pid>/toggle", methods=["POST"])
@login_required
def admin_project_toggle(pid):
    require_admin()
    p = Project.query.get_or_404(pid)
    p.is_active = not p.is_active
    db.session.commit()
    return redirect(url_for("admin_projects"))


@app.route("/admin/projects/<int:pid>/update", methods=["POST"])
@login_required
def admin_project_update(pid):
    require_admin()
    p = Project.query.get_or_404(pid)
    new_name = request.form.get("name").strip()
    if new_name and new_name != p.name:
        if Project.query.filter_by(name=new_name).first():
            flash("Projekt o takiej nazwie już istnieje.")
        else:
            p.name = new_name
            db.session.commit()
            flash("Zmieniono nazwę projektu.")
    return redirect(url_for("admin_projects"))


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
    """
    Bezpieczne przywracanie bazy z ZIP:
    - zamyka sesję i połączenia,
    - wyciąga app.db do pliku tymczasowego,
    - atomowo podmienia DB_FILE,
    - re-inicjalizuje ORM.
    """
    # Upewnij się, że wskaźnik pliku jest na początku
    try:
        fileobj.seek(0)
    except Exception:
        pass

    # Sprawdź i wyciągnij app.db z ZIP
    with zipfile.ZipFile(fileobj, "r") as z:
        names = {n: n for n in z.namelist()}
        if "app.db" not in names:
            raise RuntimeError("Brak pliku 'app.db' w archiwum.")
        with z.open("app.db") as src, tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(src.read())

    # Zamknij ORM/połączenia do obecnej bazy zanim podmienisz plik
    db.session.remove()
    try:
        db.engine.dispose()
    except Exception:
        pass

    # Podmień atomowo
    target_dir = os.path.dirname(DB_FILE) or "."
    os.makedirs(target_dir, exist_ok=True)
    final_path = os.path.join(target_dir, "app.db")
    # Na niektórych systemach trzeba usunąć istniejący plik przed rename
    if os.path.exists(final_path):
        try:
            os.remove(final_path)
        except Exception as e:
            raise RuntimeError(f"Nie mogę zastąpić istniejącej bazy: {e}")
    os.replace(tmp_path, final_path)

    # Re-inicjalizuj bazę (tabele istnieją, ale zapewniamy spójność)
    ensure_db_file()

@app.route("/admin/backup", methods=["GET"])
@login_required
def admin_backup():
    require_admin()
    base = os.path.dirname(DB_FILE)
    bdir = os.path.join(base, "backups") if base else "backups"
    os.makedirs(bdir, exist_ok=True)
    files = sorted([f for f in os.listdir(bdir) if f.endswith(".zip")])
    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Kopie zapasowe</h5>
  <form class="d-inline" method="post" action="{{ url_for('admin_backup_create') }}">
    <button class="btn btn-primary">Utwórz i pobierz kopię teraz</button>
  </form>
  <form class="d-inline ms-2" method="post" action="{{ url_for('admin_backup_create_save') }}">
    <button class="btn btn-outline-primary">Zapisz kopię na dysku serwera</button>
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
""", files=files)
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
        flash("Przywrócono bazę z załączonego pliku.")
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
        flash(f"Przywrócono bazę z {fname}.")
    except Exception as e:
        flash(f"Błąd przywracania: {e}")
    return redirect(url_for("admin_backup"))


# --- Reports (kept for completeness) ---
@app.route("/admin/reports", methods=["GET"])
@login_required
def admin_reports():
    require_admin()
    d_from = request.args.get("from")
    d_to = request.args.get("to")
    user_id = request.args.get("user_id")
    project_id = request.args.get("project_id")
    users = User.query.order_by(User.name).all()
    projects = Project.query.order_by(Project.name).all()
    rows = []
    if d_from and d_to:
        d_from_dt = datetime.strptime(d_from, "%Y-%m-%d").date()
        d_to_dt = datetime.strptime(d_to, "%Y-%m-%d").date()
        q = Entry.query.join(User).join(Project).filter(
            Entry.work_date >= d_from_dt,
            Entry.work_date <= d_to_dt
        )
        if user_id and user_id != "all":
            q = q.filter(Entry.user_id == int(user_id))
        if project_id and project_id != "all":
            q = q.filter(Entry.project_id == int(project_id))
        rows = q.order_by(Entry.work_date.asc(), Entry.id.asc()).all()
    body = render_template_string("""
<div class="card p-3">
  <h5 class="mb-3">Raport</h5>
  <form class="row g-2 mb-3" method="get">
    <div class="col-md-3">
      <label class="form-label">Od</label>
      <input class="form-control" type="date" name="from" value="{{ request.args.get('from','') }}" required>
    </div>
    <div class="col-md-3">
      <label class="form-label">Do</label>
      <input class="form-control" type="date" name="to" value="{{ request.args.get('to','') }}" required>
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
    <div class="col-12">
      <button class="btn btn-primary">Pokaż</button>
    </div>
  </form>
  {% if rows %}
  <div class="table-responsive">
    <table class="table table-sm align-middle">
      <thead><tr><th>Data</th><th>Pracownik</th><th>Projekt</th><th>Godziny</th><th>Extra</th><th>OT</th><th>Notatka</th></tr></thead>
      <tbody>
      {% for it in rows %}
        <tr>
          <td>{{ it.work_date.isoformat() }}</td>
          <td>{{ it.user.name }}</td>
          <td>{{ it.project.name }}</td>
          <td>{{ fmt(it.minutes) }}</td>
          <td>{% if it.is_extra %}✔{% else %}-{% endif %}</td>
          <td>{% if it.is_overtime %}✔{% else %}-{% endif %}</td>
          <td>{{ it.note or '' }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>
""", rows=[],
    users=User.query.order_by(User.name).all(),
    projects=Project.query.order_by(Project.name).all(),
    fmt=fmt_hhmm)
    return layout("Raport", body)


if __name__ == "__main__":
    ensure_db_file()
    app.run(debug=True, host="0.0.0.0")
