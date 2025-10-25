# NOTE: SyntaxError remains at line 929, col 49: invalid syntax
# NOTE: SyntaxError still detected at line 592, offset 28: invalid syntax. Perhaps you forgot a comma?
from datetime import datetime, date, timedelta
from dateutil import parser as dtparse
from flask import Flask, request, redirect, url_for, flash, make_response, send_file
from flask import render_template_string, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, current_user, logout_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import csv, io, os, math
from openpyxl import Workbook

# --- CONFIG ---
APP_TITLE = "EKKO NOR AS – Rejestracja czasu"
DB_PATH = os.getenv("DATABASE_URL", "sqlite:///ekko_time.db")
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret")  # ZMIEŃ w produkcji
BACKUPS_DIR = os.path.join(os.getcwd(), 'backups')
os.makedirs(BACKUPS_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = DB_PATH
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

# --- MODELS ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    active = db.Column(db.Boolean, default=True)
    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    minutes = db.Column(db.Integer, nullable=False)  # przechowujemy w minutach
    note = db.Column(db.String(400))
    # Flagi wymagane: godziny extra i nadgodziny (per wpis)
    is_extra = db.Column(db.Boolean, default=False)
    is_overtime = db.Column(db.Boolean, default=False)

    user = db.relationship("User", backref="entries")
    project = db.relationship("Project")

# --- INIT ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email="admin@local").first():
        admin = User(email="admin@local", name="Administrator", is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)
    if Project.query.count() == 0:
        db.session.add(Project(name="Projekt domyślny"))
    db.session.commit()

@login_manager.user_loader
def load_user(uid): return User.query.get(int(uid))

# --- UI helpers ---

def layout(title, body):
    # Compute current user's monthly totals for footer
    month_total = month_extra = month_ot = 0
    try:
        if current_user.is_authenticated:
            today = date.today()
            from calendar import monthrange
            d_from = date(today.year, today.month, 1)
            d_to = date(today.year, today.month, monthrange(today.year, today.month)[1])
            rows = (Entry.query.filter(Entry.user_id==current_user.id,
                                       Entry.work_date>=d_from,
                                       Entry.work_date<=d_to).all())
            month_total = sum(r.minutes for r in rows)
            month_extra = sum(r.minutes for r in rows if r.is_extra)
            month_ot = sum(r.minutes for r in rows if r.is_overtime)
    except Exception:
        pass
    base = """
<!doctype html><html lang="pl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
  :root{--bg:#f3f4f6;--card:#ffffff;--muted:#e5e7eb;--text:#222;--text-dim:#555;}
  body{background:var(--bg);color:var(--text);}
  .navbar{background:#ffffff !important; border-bottom:1px solid var(--muted);}
  .navbar .btn{margin-left:.25rem;margin-right:.25rem;}
  .card{background:var(--card);color:var(--text);border:1px solid var(--muted);border-radius:14px; box-shadow:0 2px 8px rgba(0,0,0,.04);}
  .table{color:var(--text);}
  .table thead{background:#fafafa;}
  .table td,.table th{border-color:#e5e7eb;}
  .form-control,.form-select{background:#fff;color:var(--text);border-color:#d1d5db;}
  .form-control:focus,.form-select:focus{background:#fff;color:var(--text);border-color:#94a3b8;box-shadow:none;}
  .alert{border-radius:12px;}
  .btn{border-radius:10px;padding:.42rem .7rem;}
  .badge{border-radius:10px;}
  footer.footer-summary{position:sticky;bottom:0;background:#ffffff;border-top:1px solid var(--muted);padding:.5rem 0;margin-top:1rem;z-index:10;}
  @media (max-width:576px){
    .navbar .btn,.navbar span{font-size:.95rem}
    .table{font-size:.95rem}
    .btn{padding:.35rem .55rem}
  }

  /* --- Watermark flags (PL & NO) --- */
  body::before{
    content:"";
    position:fixed;
    inset:0;
    background-image: url('data:image/svg+xml;utf8,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2016%2010'><rect%20width='16'%20height='5'%20y='0'%20fill='white'/><rect%20width='16'%20height='5'%20y='5'%20fill='%23dc143c'/></svg>'), url('data:image/svg+xml;utf8,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2022%2016'><rect%20width='22'%20height='16'%20fill='%23ba0c2f'/><rect%20x='0'%20y='6'%20width='22'%20height='4'%20fill='white'/><rect%20x='6'%20y='0'%20width='4'%20height='16'%20fill='white'/><rect%20x='0'%20y='6.8'%20width='22'%20height='2.4'%20fill='%2300205b'/><rect%20x='6.8'%20y='0'%20width='2.4'%20height='16'%20fill='%2300205b'/></svg>');
    background-repeat: no-repeat, no-repeat;
    background-position: left 40%, right 40%;
    background-size: min(32vw, 520px) auto, min(32vw, 520px) auto;
    opacity: .07;
    pointer-events: none;
    z-index: 0;
  }
  main, nav, .card, .navbar, footer{ position: relative; z-index: 1; }

</style>
</head><body>
<nav class="navbar navbar-expand-lg">
  <div class="container">
    <a class="navbar-brand" href="{{ url_for('dashboard') }}"><img src="{{ url_for('static', filename='logo.png') }}" alt="EKKO NOR AS" style="height:36px; vertical-align:middle; margin-right:8px;"></a>
    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#nav" aria-controls="nav" aria-expanded="false" aria-label="Toggle navigation">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div id="nav" class="collapse navbar-collapse justify-content-end">
      <div class="d-flex align-items-center flex-wrap">
        {% if current_user.is_authenticated %}
          <span class="me-2">Użytkownik: {{ current_user.name }}</span>
          <a class="btn btn-sm btn-success" href="{{ url_for(\'user_add_entry\') }}">Dodaj godziny</a>
          <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('entries_view') }}">Wpisy</a>
          <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('summary_view') }}">Podsumowanie</a>
          {% if current_user.is_admin %}
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_projects') }}">Projekty</a>
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_users') }}">Pracownicy</a>
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_reports') }}">Raport</a>
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_monthly') }}">Miesięczne</a>
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for(\'admin_add_entry\') }}">Dodaj godziny</a>
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('admin_backup') }}">Kopia/Przywrócenie</a>
          {% endif %}
          <a class="btn btn-sm btn-danger" href="{{ url_for('logout') }}">Wyloguj</a>
        {% else %}
          <a class="btn btn-sm btn-primary" href="{{ url_for('login') }}">Logowanie</a>
        {% endif %}
      </div>
    </div>
  </div>
</nav>
<main class="container py-3">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-info">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
  {{ body|safe }}
</main>
<footer class="footer-summary">
  <div class="container d-flex flex-wrap gap-2">
    <span class="badge bg-secondary">Miesiąc razem: {{ fmt(month_total) }}</span>
    <span class="badge bg-info text-dark">Extra: {{ fmt(month_extra) }}</span>
    <span class="badge bg-warning text-dark">Nadgodziny: {{ fmt(month_ot) }}</span>
  </div>
</footer>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
</body></html>
"""
    return render_template_string(base, title=title, body=body, fmt=fmt_hhmm, month_total=month_total, month_extra=month_extra, month_ot=month_ot)


def parse_hhmm(s: str) -> int:
    s = s.strip()
    if ":" not in s:
        hours = float(s.replace(",", "."))
        return int(round(hours * 60))
    hh, mm = s.split(":", 1)
    return int(hh) * 60 + int(mm)

def fmt_hhmm(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    m = abs(minutes)
    return f"{sign}{m//60}:{m%60:02d}"

# --- AUTH ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        pw = request.form["password"]
        u = User.query.filter_by(email=email, active=True).first()
        if u and u.check_password(pw):
            login_user(u)
            return redirect(url_for("dashboard"))
        flash("Błędne dane logowania lub konto nieaktywne.")
    body = f"""
<div class="row justify-content-center">
  <div class="col-md-6">
    <div class="card shadow-sm">
      <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
      <div class="card-body">
        <div class="text-center mb-3">
          <img src="{{ url_for(\'static\', filename=\'logo.png\') }}" alt="EKKO NOR AS" style="height:60px;">
        </div>
<p class="text-muted">System rejestracji czasu pracy dla Ekko Nor AS.</p><p class="text-muted" style="color:#bdbdbd !important">System rejestracji czasu pracy dla Ekko Nor AS.</p>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Email</label>
            <input class="form-control" name="email" required>
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
</div>"""
    return layout("Logowanie – EKKO NOR AS", body)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --- DASHBOARD ---
@app.route("/")
@login_required
def dashboard():
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    today = date.today()
    body = render_template_string(""")
<div class="row g-4">
  <div class="col-lg-6">
    <div class="card">
      <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
      <div class="card-body">
        <form method="post" action="{{ url_for('add_entry') }}">
          <div class="mb-3">
            <label class="form-label">Data</label>
            <input class="form-control" type="date" name="work_date" value="{{ today }}" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Projekt</label>
            <select class="form-select" name="project_id" required>
              {% for p in projects %}
                <option value="{{ p.id }}">{{ p.name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Czas (HH:MM, np. 1:30)</label>
            <input class="form-control" name="hhmm" placeholder="0:30, 1:00, 2:15..." required>
          </div>
          <div class="mb-3">
            <label class="form-label">Notatka (opcjonalnie)</label>
            <input class="form-control" name="note">
          </div>
          <div class="mb-3 form-check">
            <input class="form-check-input" type="checkbox" name="is_extra" id="extra">
            <label class="form-check-label" for="extra">Godziny extra</label>
          </div>
          <div class="mb-3 form-check">
            <input class="form-check-input" type="checkbox" name="is_overtime" id="overtime">
            <label class="form-check-label" for="overtime">Nadgodziny</label>
          </div>
          <button class="btn btn-success">Zapisz</button>
        </form>
      </div>
    </div>
  </div>

  <div class="col-lg-6">
    <div class="card">
      <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
      <div class="card-body">
        <iframe src="{{ url_for('day_view', day=today.isoformat()) }}" style="width:100%;height:350px;border:0;"></iframe>
      </div>
    </div>
  </div>
</div>
""", projects=projects, today=today)
    return layout("Panel", body)

@app.route("/entry/add", methods=["POST"])
@login_required
def add_entry():
    work_date = dtparse.parse(request.form["work_date"]).date()
    project_id = int(request.form["project_id"])
    minutes = parse_hhmm(request.form["hhmm"])
    note = request.form.get("note") or None
    is_extra = bool(request.form.get("is_extra"))
    is_overtime = bool(request.form.get("is_overtime"))

    proj = Project.query.get(project_id)
    if not proj or not proj.is_active:
        flash("Nieprawidłowy projekt.")
        return redirect(url_for("dashboard"))

    e = Entry(user_id=current_user.id, project_id=project_id, work_date=work_date,
              minutes=minutes, note=note, is_extra=is_extra, is_overtime=is_overtime)
    db.session.add(e)
    db.session.commit()
    flash("Dodano wpis.")
    return redirect(url_for("dashboard"))

# --- VIEWS: day & recent ---
@app.route("/day/<day>")
@login_required
def day_view(day):
    d = dtparse.parse(day).date()
    rows = (Entry.query
            .filter_by(user_id=current_user.id, work_date=d)
            .order_by(Entry.id.asc()).all())
    total = sum(r.minutes for r in rows)
    extra = sum(r.minutes for r in rows if r.is_extra)
    overtime = sum(r.minutes for r in rows if r.is_overtime)
    body = render_template_string(""")
<h5>{{ d.strftime('%Y-%m-%d') }}</h5>
<table class="table table-sm align-middle">
  <thead><tr><th>Projekt</th><th>Notatka</th><th>HH:MM</th><th>Extra</th><th>Nadgodziny</th><th></th></tr></thead>
  <tbody>
  {% for r in rows %}
    <tr>
      <td>{{ r.project.name }}</td>
      <td>{{ r.note or '' }}</td>
      <td>{{ fmt(r.minutes) }}</td>
      <td>{% if r.is_extra %}✔{% else %}-{% endif %}</td>
      <td>{% if r.is_overtime %}✔{% else %}-{% endif %}</td>
      <td class="text-end">
        <a class="btn btn-sm btn-outline-primary" href="{{ url_for('edit_entry', entry_id=r.id) }}">Edytuj</a>
        <form class="d-inline" method="post" action="{{ url_for('delete_entry', entry_id=r.id) }}" onsubmit="return confirm('Usunąć wpis?')">
          <button class="btn btn-sm btn-outline-danger">Usuń</button>
        </form>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<div class="mt-2">
  <span class="badge bg-secondary me-2">Suma: {{ fmt(total) }}</span>
  <span class="badge bg-info text-dark me-2">Extra: {{ fmt(extra) }}</span>
  <span class="badge bg-warning text-dark">Nadgodziny: {{ fmt(overtime) }}</span>
</div>
""", d=d, rows=rows, fmt=fmt_hhmm, total=total, extra=extra, overtime=overtime)
    return layout("Dzień", body)


@app.route("/entries")
@login_required
def entries_view():
    from datetime import date
    import calendar
    # Month selector: default to current month unless ?ym=YYYY-MM provided
    today = date.today()
    ym = request.args.get("ym")
    if ym:
        try:
            year, month = map(int, ym.split("-"))
        except Exception:
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    q = (Entry.query.filter(Entry.user_id==current_user.id,
                            Entry.work_date>=first_day,
                            Entry.work_date<=last_day)
                      .order_by(Entry.work_date.desc(), Entry.id.asc()).all())

    # group by day
    byday = {}
    for r in q:
        byday.setdefault(r.work_date, []).append(r)

    def totals_for_day(rows):
        mins = [r.minutes for r in rows]
        return sum(mins)

    body = render_template_string(""")
<h5>Moje wpisy – {{ '%04d-%02d' % (year, month) }}</h5>
<a class="btn btn-sm btn-success" href="{{ url_for('user_add_entry') }}">Dodaj godziny</a>
<div class="mb-3">
  <form class="row g-2 align-items-end" method="get" action="{{ url_for('entries_view') }}">
    <div class="col-auto">
      <label class="form-label">Miesiąc</label>
      <input class="form-control" type="month" name="ym" value="{{ '%04d-%02d' % (year, month) }}">
    </div>
    <div class="col-auto">
      <button class="btn btn-outline-secondary">Pokaż</button>
    </div>
    <div class="col-auto">
      <form class="d-inline" method="get" action="{{ url_for('export_csv') }}">
        <label class="form-label me-2">Eksport CSV</label>
        <input type="date" name="from" value="{{ first_day }}" required>
        <input type="date" name="to" value="{{ last_day }}" required>
        <button class="btn btn-sm btn-outline-secondary">Pobierz CSV</button>
      </form>
      <form class="d-inline ms-2" method="get" action="{{ url_for('export_xlsx') }}">
        <label class="form-label me-2">Eksport Excel</label>
        <input type="date" name="from" value="{{ first_day }}" required>
        <input type="date" name="to" value="{{ last_day }}" required>
        <button class="btn btn-sm btn-outline-success">Pobierz XLSX</button>
      </form>
    </div>
  </form>
</div>

{% for d, rows in byday.items()|sort(reverse=True) %}
  <h6 class="mt-3">{{ d.strftime('%Y-%m-%d') }}</h6>
  <table class="table table-sm">
    <thead><tr><th>Projekt</th><th>Notatka</th><th>HH:MM</th><th>Extra</th><th>Nadgodziny</th><th class="text-end">Akcje</th></tr></thead>
    <tbody>
    {% for r in rows %}
      <tr>
        <td>{{ r.project.name }}</td>
        <td>{{ r.note or '' }}</td>
        <td>{{ fmt(r.minutes) }}</td>
        <td>{% if r.is_extra %}✔{% else %}-{% endif %}</td>
        <td>{% if r.is_overtime %}✔{% else %}-{% endif %}</td>
        <td class="text-end">
          <a class="btn btn-sm btn-outline-primary me-1" href="{{ url_for('edit_entry', entry_id=r.id) }}">Edytuj</a>
          <form class="d-inline" method="post" action="{{ url_for('delete_entry', entry_id=r.id) }}" onsubmit="return confirm('Usunąć wpis?')">
            <button class="btn btn-sm btn-outline-danger">Usuń</button>
          </form>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% set raw = totals_for_day(rows) %}
  <div class="mb-2">
    <span class="badge bg-secondary me-2">Suma dnia: {{ fmt(raw) }}</span>
  </div>
{% endfor %}
""", byday=byday, fmt=fmt_hhmm, totals_for_day=totals_for_day, year=year, month=month, first_day=first_day.isoformat(), last_day=last_day.isoformat())
    return layout("Wpisy", body)

# --- EDIT/DELETE ---
@app.route("/entry/<int:entry_id>/edit", methods=["GET","POST"])
@login_required
def edit_entry(entry_id):
    r = Entry.query.get_or_404(entry_id)
    if r.user_id != current_user.id and not current_user.is_admin: abort(403)
    if request.method == "POST":
        r.work_date = dtparse.parse(request.form["work_date"]).date()
        r.project_id = int(request.form["project_id"])
        r.minutes = parse_hhmm(request.form["hhmm"])
        r.note = request.form.get("note") or None
        r.is_extra = bool(request.form.get("is_extra"))
        r.is_overtime = bool(request.form.get("is_overtime"))
        db.session.commit()
        flash("Zapisano.")
        return redirect(url_for("entries_view"))
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    body = render_template_string(""")
<div class="card">
  <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
  <div class="card-body">
    <form method="post">
      <div class="mb-3">
        <label class="form-label">Data</label>
        <input class="form-control" type="date" name="work_date" value="{{ r.work_date.isoformat() }}" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Projekt</label>
        <select class="form-select" name="project_id">
          {% for p in projects %}
            <option value="{{ p.id }}" {% if p.id==r.project_id %}selected{% endif %}>{{ p.name }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="mb-3">
        <label class="form-label">Czas (HH:MM)</label>
        <input class="form-control" name="hhmm" value="{{ fmt(r.minutes) }}" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Notatka</label>
        <input class="form-control" name="note" value="{{ r.note or '' }}">
      </div>
      <div class="mb-3 form-check">
        <input class="form-check-input" type="checkbox" name="is_extra" id="extra" {% if r.is_extra %}checked{% endif %}>
        <label class="form-check-label" for="extra">Godziny extra</label>
      </div>
      <div class="mb-3 form-check">
        <input class="form-check-input" type="checkbox" name="is_overtime" id="ot" {% if r.is_overtime %}checked{% endif %}>
        <label class="form-check-label" for="ot">Nadgodziny</label>
      </div>
      <button class="btn btn-primary">Zapisz</button>
    </form>
  </div>
</div>
""", r=r, projects=projects, fmt=fmt_hhmm)
    return layout("Edytuj wpis", body)

@app.route("/entry/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_entry(entry_id):
    r = Entry.query.get_or_404(entry_id)
    if r.user_id != current_user.id and not current_user.is_admin: abort(403)
    db.session.delete(r)
    db.session.commit()
    flash("Usunięto wpis.")
    return redirect(url_for("entries_view"))

# --- EXPORTS (user) ---
def _rows_for_user_in_range(user_id, d_from, d_to):
    return (Entry.query.filter(Entry.user_id==user_id,
                               Entry.work_date>=d_from,
                               Entry.work_date<=d_to)
                      .order_by(Entry.work_date.asc(), Entry.id.asc()).all())

@app.route("/export")
@login_required
def export_csv():
    try:
        d_from = dtparse.parse(request.args["from"]).date()
        d_to = dtparse.parse(request.args["to"]).date()
    except Exception:
        flash("Błędny zakres dat.")
        return redirect(url_for("entries_view"))

    rows = _rows_for_user_in_range(current_user.id, d_from, d_to)
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["Date","Project","Note","Minutes","HH:MM","Extra","Overtime"])
    for it in rows:
        cw.writerow([
            it.work_date.isoformat(),
            it.project.name,
            it.note or "",
            it.minutes,
            fmt_hhmm(it.minutes),
            "1" if it.is_extra else "0",
            "1" if it.is_overtime else "0",
        ])
    output = make_response(si.getvalue())
    filename = f"worktimes_{current_user.name}_{d_from}_{d_to}.csv"
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

@app.route("/export.xlsx")
@login_required
def export_xlsx():
    try:
        d_from = dtparse.parse(request.args["from"]).date()
        d_to = dtparse.parse(request.args["to"]).date()
    except Exception:
        flash("Błędny zakres dat.")
        return redirect(url_for("entries_view"))

    rows = _rows_for_user_in_range(current_user.id, d_from, d_to)
    wb = Workbook()
    ws = wb.active
    ws.title = "Wpisy"
    ws.append(["Date","Project","Note","Minutes","HH:MM","Extra","Overtime"])
    for it in rows:
        ws.append([it.work_date.isoformat(),
                   it.project.name,
                   it.note or "",
                   it.minutes,
                   fmt_hhmm(it.minutes),
                   "YES" if it.is_extra else "",
                   "YES" if it.is_overtime else ""])

    tmp_path = f"export_{current_user.id}_{d_from}_{d_to}.xlsx"
    wb.save(tmp_path)
    return send_file(tmp_path, as_attachment=True, download_name=f"worktimes_{current_user.name}_{d_from}_{d_to}.xlsx")

# --- ADMIN: PROJECTS ---
@app.route("/admin/projects", methods=["GET","POST"])
@login_required
def admin_projects():
    if not current_user.is_admin: abort(403)
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            db.session.add(Project(name=name, is_active=True))
            db.session.commit()
            flash("Dodano projekt.")
        return redirect(url_for("admin_projects"))
    projects = Project.query.order_by(Project.is_active.desc(), Project.name).all()
    body = render_template_string("""
<div class="row g-4">
  <div class="col-lg-7">
    <div class="card">
      <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
      <div class="card-body">
        <form class="row g-2" method="post">
          <div class="col-8"><input class="form-control" name="name" placeholder="Nazwa projektu" required></div>
          <div class="col-4"><button class="btn btn-primary w-100">Dodaj</button></div>
        </form>
        <hr>
        <table class="table table-sm">
          <thead><tr><th>Nazwa</th><th>Status</th><th class="text-end">Akcje</th></tr></thead>
          <tbody>
          {% for p in projects %}
            <tr>
              <td>{{ p.name }}</td>
              <td>{% if p.is_active %}<span class="badge bg-success">aktywne</span>{% else %}<span class="badge bg-secondary">archiwalne</span>{% endif %}</td>
              <td class="text-end">
                <form class="d-inline" method="post" action="{{ url_for('update_project', pid=p.id) }}">
                  <input class="form-control form-control-sm d-inline-block" style="width:200px" name="name" value="{{ p.name }}">
                  <button class="btn btn-sm btn-outline-success">Zapisz</button>
                </form>
                <a class="btn btn-sm btn-outline-secondary ms-1" href="{{ url_for('toggle_project', pid=p.id) }}">Aktywuj/Archiwizuj</a>
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
""", projects=projects)
    return layout("Projekty (Admin)", body)

@app.route("/admin/projects/<int:pid>/toggle")
@login_required
def toggle_project(pid):
    if not current_user.is_admin: abort(403)
    p = Project.query.get_or_404(pid)
    p.is_active = not p.is_active
    db.session.commit()
    return redirect(url_for("admin_projects"))

# --- ADMIN: USERS ---

