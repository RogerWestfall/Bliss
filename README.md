# Bliss

A daily email newsletter delivering positivity, progress, and possibility — straight to your inbox.

## Sections

| Section | Description |
|---|---|
| 💬 **Quote of the Day** | An inspiring or thought-provoking quote |
| 🌍 **Good News** | A positive news story with image and link |
| 🤖 **Impactful AI** | AI being used for good in the world |

## How it works

1. **Quote** — fetched from [ZenQuotes](https://zenquotes.io/) (Claude fallback if unavailable)
2. **Good News** — pulled from positive news RSS feeds, then Claude picks the best story and writes a warm blurb
3. **Impactful AI** — pulled from tech news RSS feeds, filtered for AI-for-good stories, Claude selects and summarizes
4. HTML email rendered from a Jinja2 template and sent via SMTP
5. GitHub Actions runs the whole pipeline daily at 9 AM UTC

## Setup

### 1. Clone and install

```bash
git clone https://github.com/rogerwestfall/bliss.git
cd bliss
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `SMTP_HOST` | SMTP server (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (usually `587`) |
| `SMTP_USERNAME` | Your email address |
| `SMTP_PASSWORD` | App password (not your main password) |
| `SENDER_NAME` | Display name (e.g. `Bliss Daily`) |
| `SENDER_EMAIL` | Sender email address |
| `NEWSLETTER_RECIPIENTS` | Comma-separated list of recipient emails |

> **Gmail users:** Enable 2FA and create an [App Password](https://myaccount.google.com/apppasswords) — use that as `SMTP_PASSWORD`.

### 3. Preview locally

```bash
python main.py --preview --output preview.html
# Open preview.html in a browser
```

### 4. Send a test email

```bash
python main.py
```

### 5. Automate with GitHub Actions

Add all the environment variables as **repository secrets** in GitHub:

`Settings → Secrets and variables → Actions → New repository secret`

The workflow at `.github/workflows/daily_newsletter.yml` runs automatically at **9:00 AM UTC** every day.

You can also trigger it manually via `Actions → Send Bliss Daily Newsletter → Run workflow`, with an option to preview only (saves the HTML as a downloadable artifact).

## Project structure

```
bliss/
├── main.py                          # Entry point
├── requirements.txt
├── .env.example
├── newsletter/
│   ├── config.py                    # Environment config
│   ├── content.py                   # RSS fetching + Claude summarization
│   ├── renderer.py                  # Jinja2 HTML rendering
│   ├── sender.py                    # SMTP email sending
│   └── templates/
│       └── newsletter.html          # Email HTML template
└── .github/
    └── workflows/
        └── daily_newsletter.yml     # GitHub Actions scheduler
```
