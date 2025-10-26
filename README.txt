
EKKO NOR AS – Rejestrator czasu pracy (v57 FULL)
- Admin może dodawać/edytować/usuwać wpisy godzin dla dowolnego pracownika (Godziny admin).
- Zarządzanie pracownikami (dodawanie, edycja, reset hasła).
- Projekty (dodawanie).
- Backup & Restore Render-safe (EXDEV fix).
- Czas w formacie HH:MM.

Start lokalny:
  python -m venv venv
  # Windows: venv\Scripts\activate
  # macOS/Linux: source venv/bin/activate
  pip install -r requirements.txt
  python app.py

Render.com:
  - Dodaj Disk i zamontuj pod /var/data
  - Start command: gunicorn app:app

Logowanie startowe:
  admin@local / admin123
