# EKKO NOR AS – Rejestracja Czasu Pracy

Lekka aplikacja web (Flask) do ewidencji czasu pracy:
- Konta użytkowników (hasła hashowane, dodaje TYLKO administrator).
- Projekty (admin): tworzenie/aktywacja.
- Wpisy czasu przez pracowników (data, projekt, HH:MM, notatka).
- Flagi per wpis: **Extra** oraz **Nadgodziny**.
- Widoki dzienne/ostatnie 14 dni.
- Eksport CSV i **Excel (.xlsx)** (użytkownik i admin, z zakresem dat).
- Raport admina: wszystkie wpisy pracowników w zakresie dat (z filtrami).
- **Kopia zapasowa** (pobranie pliku bazy SQLite) i **przywracanie** (wgranie pliku).
- Branding logowania: **EKKO NOR AS**.

## Szybki start
```bash
python -m venv .venv
. .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Wejdź na http://127.0.0.1:5000

Domyślny admin (tworzy się przy pierwszym starcie):
- login: `admin@local`
- hasło: `admin123`

## Środowisko produkcyjne
- Zmień `SECRET_KEY` w `app.py`.
- Użyj serwera aplikacyjnego (gunicorn/uwsgi) za reverse proxy.
- Rozważ Postgres w miejsce SQLite przy wielu użytkownikach równolegle.

## Kopia zapasowa / przywrócenie
- **Admin → Kopia/Przywrócenie**.
- „Pobierz kopię” zapisze `ekko_time.db`.
- „Przywróć”: wgraj poprzednio pobraną bazę (zastąpi aktualną). Operacja wymaga uprawnień admina.
