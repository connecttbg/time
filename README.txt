
EKKO NOR AS – Rejestrator czasu pracy (v56 Render ready)
Najważniejsze: naprawa przywracania kopii na Render.com (EXDEV / cross-device).

Start lokalny:
  python -m venv venv
  # Windows: venv\Scripts\activate
  # macOS/Linux: source venv/bin/activate
  pip install -r requirements.txt
  python app.py

Render.com:
  - Dodaj Disk i zamontuj pod /var/data
  - Sekretne zmienne nie są wymagane (SECRET_KEY możesz dodać samodzielnie)
  - Start Command: gunicorn app:app

Logowanie startowe:
  admin@local / admin123
