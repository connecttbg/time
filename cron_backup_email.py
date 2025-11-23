import os
import io
import zipfile
from datetime import datetime
import smtplib
from email.message import EmailMessage


# Ścieżka do pliku bazy danych (taka sama logika jak w app.py)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, "app.db")


def create_backup_zip() -> io.BytesIO:
    """Tworzy ZIP z plikiem bazy danych i zwraca go w pamięci."""
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(f"Nie znaleziono bazy danych: {DB_FILE}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(DB_FILE, arcname="app.db")
    buf.seek(0)
    return buf


def send_backup_email():
    """Wysyła kopię bazy na e-mail z użyciem zmiennych środowiskowych SMTP_* i BACKUP_EMAIL_*."""

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("BACKUP_EMAIL_TO")
    email_from = os.getenv("BACKUP_EMAIL_FROM") or smtp_user

    if not all([smtp_host, smtp_port, smtp_user, smtp_password, email_to, email_from]):
        raise RuntimeError("Brak wymaganych zmiennych środowiskowych SMTP / BACKUP_EMAIL_*.")

    backup_buf = create_backup_zip()

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    subject = f"EKKO NOR – kopia bazy {now_str}"
    filename = f"app_backup_{now_str}.zip"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    msg.set_content(
        "Kopia zapasowa bazy danych aplikacji EKKO NOR.\n"
        "Ta wiadomość została wygenerowana automatycznie przez system (cron).\n"
        "Jeśli nie oczekiwałeś tej wiadomości, możesz ją zignorować."
    )

    msg.add_attachment(
        backup_buf.getvalue(),
        maintype="application",
        subtype="zip",
        filename=filename,
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

    print(f"Wysłano kopię zapasową na adres: {email_to}")


if __name__ == "__main__":
    send_backup_email()
