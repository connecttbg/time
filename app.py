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
DB_PATH = "sqlite:///ekko_time.db"
SECRET_KEY = "change-me-please"  # ZMIEŃ w produkcji
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

# --- HEALTH CHECK (no auth) ---
@app.route("/healthz")
def healthz():
    return "OK", 200

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
    <a class="navbar-brand smallcaps" href="{{ url_for('dashboard') }}">EKKO NOR AS</a>
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
      <div class="card-body"><p class="text-muted">System rejestracji czasu pracy dla Ekko Nor AS.</p><p class="text-muted" style="color:#bdbdbd !important">System rejestracji czasu pracy dla Ekko Nor AS.</p>
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

@app.route("/admin/projects/<int:pid>/update", methods=["POST"], endpoint="update_project")
@login_required
def update_project(pid):
    if not current_user.is_admin: abort(403)
    p = Project.query.get_or_404(pid)
    new_name = (request.form.get("name") or "").strip()
    if not new_name:
        flash("Nazwa nie może być pusta.")
        return redirect(url_for("admin_projects"))
    if new_name != p.name:
        exists = Project.query.filter(Project.id!=p.id, Project.name==new_name).first()
        if exists:
            flash("Projekt o takiej nazwie już istnieje.")
            return redirect(url_for("admin_projects"))
        p.name = new_name
        db.session.commit()
        flash("Zmieniono nazwę projektu.")
    else:
        flash("Bez zmian.")
    return redirect(url_for("admin_projects"))

@app.route("/admin/users", methods=["GET","POST"])
@login_required
def admin_users():
    if not current_user.is_admin: abort(403)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            email = request.form["email"].strip().lower()
            name = request.form["name"].strip()
            pw = request.form["password"]
            u = User(email=email, name=name, is_admin=False, active=True)
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            flash("Utworzono użytkownika.")
        elif action == "update":
            uid = int(request.form["uid"])
            u = User.query.get_or_404(uid)
            u.name = (request.form.get("name") or u.name).strip()
            new_email = (request.form.get("email") or u.email).strip().lower()
            if new_email != u.email:
                if User.query.filter(User.id!=u.id, User.email==new_email).first():
                    flash("Email już istnieje dla innego użytkownika.")
                else:
                    u.email = new_email
            db.session.commit()
            flash("Zaktualizowano dane pracownika.")
        elif action == "reset":
            uid = int(request.form["uid"])
            pw = request.form["password"]
            u = User.query.get_or_404(uid)
            u.set_password(pw)
            db.session.commit()
            flash("Hasło zmienione.")
        elif action == "toggle":
            uid = int(request.form["uid"])
            u = User.query.get_or_404(uid)
            u.active = not u.active
            db.session.commit()
            flash("Zmieniono status konta.")
        return redirect(url_for("admin_users"))
    users = User.query.order_by(User.is_admin.desc(), User.name).all()
    body = render_template_string(""")
<div class="row g-4">
  <div class="col-lg-8">
    <div class="card">
      <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
      <div class="card-body">
        <h6>Dodaj pracownika</h6>
        <form class="row g-2 mb-3" method="post">
          <input type="hidden" name="action" value="create">
          <div class="col-md-3"><input class="form-control" name="name" placeholder="Imię i nazwisko" required></div>
          <div class="col-md-4"><input class="form-control" name="email" placeholder="Email" required></div>
          <div class="col-md-3"><input class="form-control" type="password" name="password" placeholder="Hasło" required></div>
          <div class="col-md-2"><button class="btn btn-primary w-100">Dodaj</button></div>
        </form>
        <hr>
        <table class="table table-sm">
          <thead><tr><th>Imię i nazwisko</th><th>Email</th><th>Rola</th><th>Status</th><th class="text-end">Akcje</th></tr></thead>
          <tbody>
          {% for u in users %}
            <tr>
              <td>
                <form class="d-inline" method="post">
                  <input type="hidden" name="action" value="update">
                  <input type="hidden" name="uid" value="{{ u.id }}">
                  <input class="form-control form-control-sm d-inline-block" style="width:180px" name="name" value="{{ u.name }}">
              </td>
              <td>
                  <input class="form-control form-control-sm d-inline-block" style="width:220px" name="email" value="{{ u.email }}">
              </td>
              <td>{% if u.is_admin %}Admin{% else %}Pracownik{% endif %}</td>
              <td>{% if u.active %}<span class="badge bg-success">aktywne</span>{% else %}<span class="badge bg-secondary">zablokowane</span>{% endif %}</td>
              <td class="text-end">
                <a class="btn btn-sm btn-outline-primary me-1" href="{{ url_for('admin_add_entry') }}">Dodaj godziny</a>
                {% if not u.is_admin %}
                <button class="btn btn-sm btn-outline-success me-1">Zapisz dane</button>
                </form>
                <form class="d-inline" method="post" style="margin-right:4px;">
                  <input type="hidden" name="action" value="toggle">
                  <input type="hidden" name="uid" value="{{ u.id }}">
                  <button class="btn btn-sm btn-outline-secondary">Aktywuj/Zablokuj</button>
                </form>
                <form class="d-inline" method="post">
                  <input type="hidden" name="action" value="reset">
                  <input type="hidden" name="uid" value="{{ u.id }}">
                  <input class="form-control form-control-sm d-inline-block" style="width:150px" type="password" name="password" placeholder="Nowe hasło" required>
                  <button class="btn btn-sm btn-outline-primary">Zmień hasło</button>
                </form>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
""", users=users)
    return layout("Pracownicy (Admin)", body)

# --- ADMIN: REPORTS (all users) ---
@app.route("/admin/reports", methods=["GET","POST"])
@login_required
def admin_reports():
    if not current_user.is_admin: abort(403)
    users = User.query.order_by(User.name).all()
    projects = Project.query.order_by(Project.name).all()
    d_from = request.args.get("from")
    d_to = request.args.get("to")
    user_id = request.args.get("user_id")
    project_id = request.args.get("project_id")

    rows = []
    if d_from and d_to:
        d_from_dt = dtparse.parse(d_from).date()
        d_to_dt = dtparse.parse(d_to).date()
        q = Entry.query.join(User).join(Project).filter(
            Entry.work_date>=d_from_dt,
            Entry.work_date<=d_to_dt
        )
        if user_id and user_id != "all":
            q = q.filter(Entry.user_id==int(user_id))
        if project_id and project_id != "all":
            q = q.filter(Entry.project_id==int(project_id))
        rows = q.order_by(Entry.work_date.asc(), Entry.id.asc()).all()

    body = render_template_string(""")
<div class="card">
  <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
  <div class="card-body">
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
        <a class="btn btn-outline-primary ms-2" href="{{ url_for('admin_add_entry') }}">Dodaj godziny</a>
        {% if rows %}
          <a class="btn btn-outline-secondary ms-2" href="{{ url_for('admin_export_csv', **request.args) }}">Eksport CSV</a>
          <a class="btn btn-outline-success ms-2" href="{{ url_for('admin_export_xlsx', **request.args) }}">Eksport XLSX</a>
        {% endif %}
      </div>
    </form>

    {% if rows %}
      <table class="table table-sm align-middle">
        <thead><tr>
          <th>Data</th><th>Pracownik</th><th>Projekt</th><th>Notatka</th>
          <th>HH:MM</th><th>Extra</th><th>Nadgodziny</th><th class="text-end">Akcje</th>
        </tr></thead>
        <tbody>
        {% for it in rows %}
          <tr>
            <td>{{ it.work_date.isoformat() }}</td>
            <td>{{ it.user.name }}</td>
            <td>{{ it.project.name }}</td>
            <td>{{ it.note or '' }}</td>
            <td>{{ fmt(it.minutes) }}</td>
            <td>{% if it.is_extra %}✔{% else %}-{% endif %}</td>
            <td>{% if it.is_overtime %}✔{% else %}-{% endif %}</td>
            <td class="text-end"><a class="btn btn-sm btn-outline-primary me-1" href="{{ url_for('edit_entry', entry_id=it.id) }}">Edytuj</a><form class="d-inline" method="post" action="{{ url_for('delete_entry', entry_id=it.id) }}" onsubmit="return confirm('Usunąć wpis?')"><button class="btn btn-sm btn-outline-danger">Usuń</button></form></td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    {% endif %}
  </div>
</div>
""", users=users, projects=projects, rows=rows, fmt=fmt_hhmm)
    return layout("Raport (Admin)", body)

def _admin_query_rows(args):
    d_from_dt = dtparse.parse(args.get("from")).date()
    d_to_dt = dtparse.parse(args.get("to")).date()
    q = Entry.query.join(User).join(Project).filter(
        Entry.work_date>=d_from_dt,
        Entry.work_date<=d_to_dt
    )
    if args.get("user_id") and args.get("user_id") != "all":
        q = q.filter(Entry.user_id==int(args.get("user_id")))
    if args.get("project_id") and args.get("project_id") != "all":
        q = q.filter(Entry.project_id==int(args.get("project_id")))
    return q.order_by(Entry.work_date.asc(), Entry.id.asc()).all(), d_from_dt, d_to_dt

@app.route("/admin/export.csv")
@login_required
def admin_export_csv():
    if not current_user.is_admin: abort(403)
    rows, d_from_dt, d_to_dt = _admin_query_rows(request.args)
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["Date","User","Project","Note","Minutes","HH:MM","Extra","Overtime"])
    for it in rows:
        cw.writerow([it.work_date.isoformat(), it.user.name, it.project.name, it.note or "",
                     it.minutes, fmt_hhmm(it.minutes), "1" if it.is_extra else "0", "1" if it.is_overtime else "0"])
    output = make_response(si.getvalue())
    filename = f"report_{d_from_dt}_{d_to_dt}.csv"
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

@app.route("/admin/export.xlsx")
@login_required
def admin_export_xlsx():
    if not current_user.is_admin: abort(403)
    rows, d_from_dt, d_to_dt = _admin_query_rows(request.args)
    wb = Workbook()
    ws = wb.active
    ws.title = "Raport"
    ws.append(["Date","User","Project","Note","Minutes","HH:MM","Extra","Overtime"])
    for it in rows:
        ws.append([it.work_date.isoformat(), it.user.name, it.project.name, it.note or "",
                   it.minutes, fmt_hhmm(it.minutes),
                   "YES" if it.is_extra else "", "YES" if it.is_overtime else ""])
    tmp_path = f"admin_report_{d_from_dt}_{d_to_dt}.xlsx"
    wb.save(tmp_path)
    return send_file(tmp_path, as_attachment=True, download_name=f"admin_report_{d_from_dt}_{d_to_dt}.xlsx")


# --- MONTH HELPERS ---
def month_bounds(year:int, month:int):
    from datetime import date
    import calendar
    first = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    return first, last

# --- USER: MONTHLY SUMMARY ---
@app.route("/summary")
@login_required
def summary_view():
    from datetime import date
    from dateutil.relativedelta import relativedelta
    today = date.today()
    ym = request.args.get("ym")
    if ym:
        try:
            year, month = map(int, ym.split("-"))
        except Exception:
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month
    d_from, d_to = month_bounds(year, month)

    rows = (Entry.query.filter(Entry.user_id==current_user.id,
                               Entry.work_date>=d_from,
                               Entry.work_date<=d_to)
                        .order_by(Entry.work_date.asc(), Entry.id.asc()).all())
    total = sum(r.minutes for r in rows)
    total_extra = sum(r.minutes for r in rows if r.is_extra)
    total_ot = sum(r.minutes for r in rows if r.is_overtime)

    proj_map = {}
    for r in rows:
        pname = r.project.name if r.project else "(usunięty)"
        if pname not in proj_map:
            proj_map[pname] = {"minutes":0, "extra":0, "ot":0}
        proj_map[pname]["minutes"] += r.minutes
        if r.is_extra: proj_map[pname]["extra"] += r.minutes
        if r.is_overtime: proj_map[pname]["ot"] += r.minutes

    proj_rows = [(p, v["minutes"], v["extra"], v["ot"]) for p,v in proj_map.items()]
    proj_rows.sort(key=lambda x: x[0].lower())

    cur_first = d_from
    prev_first = (cur_first.replace(day=1) - relativedelta(months=1))
    next_first = (cur_first.replace(day=1) + relativedelta(months=1))

    body = render_template_string(""")
<div class="card">
  <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
  <div class="card-body">
    <form class="row g-2 mb-3" method="get">
      <div class="col-md-4">
        <label class="form-label">Miesiąc (RRRR-MM)</label>
        <input class="form-control" type="month" name="ym" value="{{ '%04d-%02d' % (year, month) }}">
      </div>
      <div class="col-md-2 d-flex align-items-end">
        <button class="btn btn-primary">Pokaż</button>
      </div>
      <div class="col-md-6 d-flex align-items-end justify-content-end">
        <a class="btn btn-outline-secondary me-2" href="{{ url_for('summary_view', ym=prev_first.strftime('%Y-%m')) }}">&laquo; Poprzedni</a>
        <a class="btn btn-outline-secondary" href="{{ url_for('summary_view', ym=next_first.strftime('%Y-%m')) }}">Następny &raquo;</a>
      </div>
    </form>

    <div class="mb-2">
      <span class="badge bg-secondary me-2">Razem: {{ fmt(total) }}</span>
      <span class="badge bg-info text-dark me-2">Extra: {{ fmt(total_extra) }}</span>
      <span class="badge bg-warning text-dark">Nadgodziny: {{ fmt(total_ot) }}</span>
    </div>

    <h6 class="mt-3">Suma wg projektu</h6>
    <table class="table table-sm align-middle">
      <thead><tr><th>Projekt</th><th>HH:MM</th><th>Extra</th><th>Nadgodziny</th></tr></thead>
      <tbody>
      {% for name, mins, ex, ot in proj_rows %}
        <tr>
          <td>{{ name }}</td>
          <td>{{ fmt(mins) }}</td>
          <td>{{ fmt(ex) }}</td>
          <td>{{ fmt(ot) }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""", year=year, month=month, total=total, total_extra=total_extra, total_ot=total_ot,
       proj_rows=proj_rows, fmt=fmt_hhmm, prev_first=prev_first, next_first=next_first)
    return layout("Podsumowanie miesiąca", body)

# --- ADMIN: MONTHLY OVERVIEW ALL USERS ---
@app.route("/admin/monthly")
@login_required
def admin_monthly():
    if not current_user.is_admin: abort(403)
    from datetime import date
    from dateutil.relativedelta import relativedelta
    today = date.today()
    ym = request.args.get("ym")
    if ym:
        try:
            year, month = map(int, ym.split("-"))
        except Exception:
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month
    d_from, d_to = month_bounds(year, month)

    users = User.query.order_by(User.name).all()
    user_totals = []
    for u in users:
        rows = (Entry.query.filter(Entry.user_id==u.id,
                                   Entry.work_date>=d_from,
                                   Entry.work_date<=d_to).all())
        total = sum(r.minutes for r in rows)
        extra = sum(r.minutes for r in rows if r.is_extra)
        ot = sum(r.minutes for r in rows if r.is_overtime)
        user_totals.append((u, total, extra, ot))

    prev_first = (d_from.replace(day=1) - relativedelta(months=1))
    next_first = (d_from.replace(day=1) + relativedelta(months=1))

    body = render_template_string(""")
<div class="card">
  <div class="card-header text-center"><h5 class="m-0">Ekko Nor AS – Rejestrator czasu pracy</h5></div>
  <div class="card-body">
    <form class="row g-2 mb-3" method="get">
      <div class="col-md-4">
        <label class="form-label">Miesiąc (RRRR-MM)</label>
        <input class="form-control" type="month" name="ym" value="{{ '%04d-%02d' % (year, month) }}">
      </div>
      <div class="col-md-2 d-flex align-items-end">
        <button class="btn btn-primary">Pokaż</button>
      </div>
      <div class="col-md-6 d-flex align-items-end justify-content-end">
        <a class="btn btn-outline-secondary me-2" href="{{ url_for('admin_monthly', ym=prev_first.strftime('%Y-%m')) }}">&laquo; Poprzedni</a>
        <a class="btn btn-outline-secondary" href="{{ url_for('admin_monthly', ym=next_first.strftime('%Y-%m')) }}">Następny &raquo;</a>
      </div>
    </form>

    <table class="table table-sm align-middle">
      <thead><tr><th>Pracownik</th><th>Razem HH:MM</th><th>Extra</th><th>Nadgodziny</th></tr></thead>
      <tbody>
      {% for u, total, ex, ot in user_totals %}
        <tr>
          <td>{{ u.name }}</td>
          <td>{{ fmt(total) }}</td>
          <td>{{ fmt(ex) }}</td>
          <td>{{ fmt(ot) }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""", year=year, month=month, user_totals=user_totals, fmt=fmt_hhmm,
       prev_first=prev_first, next_first=next_first)
    return layout("Podsumowanie miesięczne (Admin)", body)



# --- ADMIN: ADD ENTRY FOR ANY USER ---
@app.route("/admin/entries/add", methods=["GET","POST"])
@login_required
def admin_add_entry():
    if not current_user.is_admin: abort(403)
    from datetime import date
    users = User.query.order_by(User.name).all()
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    pre_user_id = request.args.get("user_id", type=int)
    if request.method == "POST":
        user_id = int(request.form["user_id"])
        project_id = int(request.form["project_id"])
        work_date = dtparse.parse(request.form["work_date"]).date()
        minutes = parse_hhmm(request.form["hhmm"])
        note = request.form.get("note") or None
        is_extra = bool(request.form.get("is_extra"))
        is_overtime = bool(request.form.get("is_overtime"))
        u = User.query.get_or_404(user_id)
        _ = Project.query.get_or_404(project_id)
        e = Entry(user_id=user_id, project_id=project_id, work_date=work_date,
                  minutes=minutes, note=note, is_extra=is_extra, is_overtime=is_overtime)
        db.session.add(e)
        db.session.commit()
        flash(f"Dodano wpis czasu dla: {u.name}.")
        return redirect(url_for("admin_reports", **({"from": work_date.isoformat(), "to": work_date.isoformat(), "user_id": user_id})))
    body = render_template_string(""")
<div class="card">
  <div class="card-header">Dodaj godziny (Admin)</div>
  <div class="card-body">
    <form method="post">
      <div class="row g-3">
        <div class="col-md-4">
          <label class="form-label">Pracownik</label>
          <select class="form-select" name="user_id" required>
            {% for u in users %}
              <option value="{{ u.id }}" {% if pre_user_id and pre_user_id==u.id %}selected{% endif %}>{{ u.name }} ({{ u.email }})</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-4">
          <label class="form-label">Projekt</label>
          <select class="form-select" name="project_id" required>
            {% for p in projects %}
              <option value="{{ p.id }}">{{ p.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-4">
          <label class="form-label">Data</label>
          <input class="form-control" type="date" name="work_date" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-4">
          <label class="form-label">Czas (HH:MM)</label>
          <input class="form-control" name="hhmm" placeholder="np. 1:30" required>
        </div>
        <div class="col-md-8">
          <label class="form-label">Notatka (opcjonalnie)</label>
          <input class="form-control" name="note" placeholder="Opis prac...">
        </div>
        <div class="col-md-12">
          <div class="form-check form-check-inline">
            <input class="form-check-input" type="checkbox" name="is_extra" id="admin_extra">
            <label class="form-check-label" for="admin_extra">Godziny extra</label>
          </div>
          <div class="form-check form-check-inline">
            <input class="form-check-input" type="checkbox" name="is_overtime" id="admin_ot">
            <label class="form-check-label" for="admin_ot">Nadgodziny</label>
          </div>
        </div>
      </div>
      <div class="mt-3">
        <button class="btn btn-success">Zapisz</button>
        <a class="btn btn-outline-secondary" href="{{ url_for('admin_reports') }}">Anuluj</a>
      </div>
    </form>
  </div>
</div>
""", users=users, projects=projects, pre_user_id=pre_user_id, date=date)
    return layout("Dodaj godziny (Admin)", body)



# --- USER: ADD ENTRY ---
@app.route("/entries/add", methods=["GET","POST"], endpoint="user_add_entry")
@login_required
def user_add_entry():
    from datetime import date
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    if request.method == "POST":
        work_date = dtparse.parse(request.form["work_date"]).date()
        project_id = int(request.form["project_id"])
        minutes = parse_hhmm(request.form["hhmm"])
        note = request.form.get("note") or None
        is_extra = bool(request.form.get("is_extra"))
        is_overtime = bool(request.form.get("is_overtime"))

        proj = Project.query.get(project_id)
        if not proj or not proj.is_active:
            flash("Nieprawidłowy projekt.")
            return redirect(url_for("user_add_entry"))

        e = Entry(user_id=current_user.id, project_id=project_id, work_date=work_date,
                  minutes=minutes, note=note, is_extra=is_extra, is_overtime=is_overtime)
        db.session.add(e)
        db.session.commit()
        flash("Dodano wpis.")
        return redirect(url_for("entries_view"))
    body = render_template_string(""")
<div class="card">
  <div class="card-header">Dodaj godziny</div>
  <div class="card-body">
    <form method="post">
      <div class="row g-3">
        <div class="col-md-4">
          <label class="form-label">Data</label>
          <input class="form-control" type="date" name="work_date" value="{{ date.today().isoformat() }}" required>
        </div>
        <div class="col-md-4">
          <label class="form-label">Projekt</label>
          <select class="form-select" name="project_id" required>
            {% for p in projects %}
              <option value="{{ p.id }}">{{ p.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-4">
          <label class="form-label">Czas (HH:MM)</label>
          <input class="form-control" name="hhmm" placeholder="np. 1:30" required>
        </div>
        <div class="col-12">
          <label class="form-label">Notatka (opcjonalnie)</label>
          <input class="form-control" name="note" placeholder="Opis prac...">
        </div>
        <div class="col-12">
          <div class="form-check form-check-inline">
            <input class="form-check-input" type="checkbox" name="is_extra" id="user_extra">
            <label class="form-check-label" for="user_extra">Godziny extra</label>
          </div>
          <div class="form-check form-check-inline">
            <input class="form-check-input" type="checkbox" name="is_overtime" id="user_ot">
            <label class="form-check-label" for="user_ot">Nadgodziny</label>
          </div>
        </div>
      </div>
      <div class="mt-3">
        <button class="btn btn-success">Zapisz</button>
        <a class="btn btn-outline-secondary" href="{{ url_for('entries_view') }}">Anuluj</a>
      </div>
    </form>
  </div>
</div>
""", projects=projects, date=date)
    return layout("Dodaj godziny", body)

# --- ADMIN: BACKUP / RESTORE ---



@app.route("/admin/backup", methods=["GET","POST"])
@login_required
def admin_backup():
    if not current_user.is_admin: abort(403)

    # Handle restore from uploaded file (optional)
    if request.method == "POST" and 'dbfile' in request.files:
        f = request.files.get("dbfile")
        if not f or f.filename == "":
            flash("Brak pliku.")
            return redirect(url_for("admin_backup"))
        filename = secure_filename(f.filename)
        tmp_path = os.path.join(os.getcwd(), "restore_tmp.db")
        f.save(tmp_path)
        try:
            db.session.close(); db.engine.dispose()
        except Exception:
            pass
        target = os.path.join(os.getcwd(), "ekko_time.db")
        os.replace(tmp_path, target)
        flash("Przywrócono bazę z wgranego pliku. (W razie problemów uruchom aplikację ponownie.)")
        return redirect(url_for("admin_backup"))

    body = render_template_string(""")
<div class="card">
  <div class="card-header">Kopia zapasowa / Przywracanie</div>
  <div class="card-body">
    <a class="btn btn-primary" href="{{ url_for('download_backup') }}">Pobierz kopię teraz</a>
    <span class="ms-2 text-muted">Plik zostanie pobrany bez zapisywania na serwerze.</span>

    <hr>
    <h6>Przywróć z wgranego pliku</h6>
    <form method="post" enctype="multipart/form-data">
      <input class="form-control" type="file" name="dbfile" accept=".db,.sqlite,.sqlite3" required>
      <button class="btn btn-danger mt-2">Przywróć</button>
    </form>

    <p class="mt-3 text-muted">Operacja przywracania zastępuje <code>ekko_time.db</code>. Nieodwracalne.</p>
  </div>
</div>
""")
    return layout("Kopia / Przywrócenie", body)





@app.route("/admin/backup/download-now", methods=["GET"])
@login_required
def download_backup():
    if not current_user.is_admin: abort(403)
    # Ensure DB exists
    try:
        db.session.commit()
    except Exception:
        pass
    db_path = os.path.join(os.getcwd(), "ekko_time.db")
    if not os.path.exists(db_path):
        with app.app_context():
            db.create_all()
        open(db_path, "ab").close()

    with open(db_path, "rb") as f:
        data = f.read()
    from io import BytesIO
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"ekko_time_{ts}.db"
    return send_file(BytesIO(data), as_attachment=True, download_name=fname, mimetype="application/octet-stream")
# --- ADMIN: CREATE BACKUP (robust endpoint) ---
@app.route("/admin/backup/create", methods=["POST"], endpoint="create_backup")
@login_required
def __admin_create_backup_action():
    if not current_user.is_admin: abort(403)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    # Ensure DB file exists
    try:
        db.session.commit()
    except Exception:
        pass
    src = os.path.join(os.getcwd(), "ekko_time.db")
    if not os.path.exists(src):
        with app.app_context():
            db.create_all()
        open(src, "ab").close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_name = f"ekko_time_{ts}.db"
    dst = os.path.join(BACKUPS_DIR, dst_name)
    try:
        import shutil
        shutil.copy2(src, dst)
        flash(f"Utworzono kopię: {dst_name}")
    except Exception as e:
        flash(f"Błąd tworzenia kopii: {e}")
    return redirect(url_for("admin_backup"))
# --- RUN ---
if __name__ == "__main__":
    import logging, sys
    # Log to file and console
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("error.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    print(">>> Starting EKKO NOR AS Flask app on http://127.0.0.1:5000", flush=True)
    app.run(host="127.0.0.1", port=5000, debug=True)
@app.route("/admin/projects/<int:pid>/update", methods=["POST"])
@login_required
def update_project(pid):
    if not current_user.is_admin: abort(403)
    p = Project.query.get_or_404(pid)
    new_name = (request.form.get("name") or "").strip()
    if new_name and new_name != p.name:
        # sprawdź kolizję nazw
        exists = Project.query.filter(Project.id!=p.id, Project.name==new_name).first()
        if exists:
            flash("Projekt o takiej nazwie już istnieje.")
        else:
            p.name = new_name
            db.session.commit()
            flash("Zmieniono nazwę projektu.")
    return redirect(url_for("admin_projects"))




@app.route("/admin/backup/create", methods=["POST"])
@login_required
def create_backup():
    if not current_user.is_admin: abort(403)
    # Ensure DB file exists; force a commit to touch the file if needed
    try:
        db.session.commit()
    except Exception:
        pass
    src = os.path.join(os.getcwd(), "ekko_time.db")
    if not os.path.exists(src):
        # Create an empty DB by ensuring tables are created
        with app.app_context():
            db.create_all()
        # touch file
        open(src, "ab").close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_name = f"ekko_time_{ts}.db"
    dst = os.path.join(BACKUPS_DIR, dst_name)
    try:
        import shutil
        shutil.copy2(src, dst)
        flash(f"Utworzono kopię: {dst_name}")
    except Exception as e:
        flash(f"Błąd tworzenia kopii: {e}")
    return redirect(url_for("admin_backup"))


@app.route("/admin/backup/restore-from-file", methods=["POST"])
@login_required
def restore_from_backup_file():
    if not current_user.is_admin: abort(403)
    filename = request.form.get("filename")
    if not filename:
        flash("Brak nazwy pliku kopii.")
        return redirect(url_for("admin_backup"))
    src = os.path.join(BACKUPS_DIR, os.path.basename(filename))
    if not os.path.exists(src):
        flash("Wybrana kopia nie istnieje.")
        return redirect(url_for("admin_backup"))
    try:
        db.session.close(); db.engine.dispose()
    except Exception:
        pass
    dst = os.path.join(os.getcwd(), "ekko_time.db")
    try:
        import shutil
        shutil.copy2(src, dst)
        flash(f"Przywrócono z kopii: {os.path.basename(src)}")
    except Exception as e:
        flash(f"Błąd przywracania: {e}")
    return redirect(url_for("admin_backup"))








