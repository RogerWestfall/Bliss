"""Sends the newsletter via SMTP."""

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from newsletter.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    SENDER_NAME, SENDER_EMAIL, RECIPIENTS,
)

logger = logging.getLogger(__name__)


def send(html: str) -> None:
    if not RECIPIENTS:
        raise ValueError("No recipients configured. Set NEWSLETTER_RECIPIENTS env var.")

    subject = f"✨ Bliss Daily — {datetime.now().strftime('%B %d, %Y')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = ", ".join(RECIPIENTS)

    # Plain-text fallback
    plain = (
        "Bliss Daily Newsletter\n"
        f"{datetime.now().strftime('%B %d, %Y')}\n\n"
        "View this email in a browser for the full experience."
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    logger.info("Connecting to %s:%s", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, RECIPIENTS, msg.as_string())

    logger.info("Newsletter sent to: %s", ", ".join(RECIPIENTS))
