"""Sends the newsletter via SMTP."""

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from newsletter.config import (
    RECIPIENTS, SENDER_EMAIL, SENDER_NAME,
    SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME,
)

logger = logging.getLogger(__name__)


def send(html: str) -> None:
    if not RECIPIENTS:
        raise ValueError(
            "No recipients configured. Set NEWSLETTER_RECIPIENTS in your .env file."
        )

    subject = f"Bliss — {datetime.now().strftime('%B %d, %Y')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = ", ".join(RECIPIENTS)
    msg.attach(MIMEText("View this email in a browser for the full experience.", "plain"))
    msg.attach(MIMEText(html, "html"))

    logger.info("Connecting to %s:%s", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, RECIPIENTS, msg.as_string())

    logger.info("Sent to: %s", ", ".join(RECIPIENTS))
