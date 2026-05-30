import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

SENDER_NAME = os.environ.get("SENDER_NAME", "Bliss Daily")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", SMTP_USERNAME)

RECIPIENTS_RAW = os.environ.get("NEWSLETTER_RECIPIENTS", "")
RECIPIENTS = [r.strip() for r in RECIPIENTS_RAW.split(",") if r.strip()]

GOOD_NEWS_FEEDS = [
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.bbc.co.uk/news/10628494#atom.xml",
]

AI_IMPACT_FEEDS = [
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://feeds.feedburner.com/TechCrunch/AI",
]
