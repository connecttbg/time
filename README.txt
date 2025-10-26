
EKKO NOR AS – Rejestrator czasu pracy (v58 FULL)
- Projekty: dodawanie, zmiana nazwy, aktywacja/dezaktywacja, usuwanie.
- Raport: przegląd + przycisk "Eksport do Excel" (openpyxl).
- Admin: zakładka "Admin" z łączną liczbą godzin w wybranym miesiącu.
- Godziny (admin): pełne dodawanie/edycja/usuwanie wpisów dla dowolnego pracownika.
- Użytkownicy (admin): dodawanie, edycja, reset hasła.
- Kopie: tworzenie/pobieranie/zapis/przywracanie (Render-safe).

Start lokalny:
  python -m venv venv
  # Windows: venv\Scripts\activate
  # macOS/Linux: source venv/bin/activate
  pip install -r requirements.txt
  python app.py

Render.com:
  - Disk mount: /var/data
  - Start: gunicorn app:app

Login startowy:
  admin@local / admin123
