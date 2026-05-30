import os
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

SENDER_NAME = os.environ.get("SENDER_NAME", "Bliss")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", SMTP_USERNAME)

_raw = os.environ.get("NEWSLETTER_RECIPIENTS", "")
RECIPIENTS = [r.strip() for r in _raw.split(",") if r.strip()]
