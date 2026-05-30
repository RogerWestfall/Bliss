"""Fetches content for each newsletter section."""

import re
import json
import logging
import requests
import feedparser
import anthropic
from datetime import datetime
from bs4 import BeautifulSoup
from newsletter.config import ANTHROPIC_API_KEY, GOOD_NEWS_FEEDS, AI_IMPACT_FEEDS

logger = logging.getLogger(__name__)

_client = None


def _claude():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Quote of the Day
# ---------------------------------------------------------------------------

_QUOTE_APIS = [
    ("https://zenquotes.io/api/today", lambda d: {"quote": d[0]["q"], "author": d[0]["a"]}),
    ("https://api.quotable.io/random?tags=inspirational,wisdom", lambda d: {"quote": d["content"], "author": d["author"]}),
]


def fetch_quote() -> dict:
    """Returns {quote, author}, trying multiple APIs then falling back to Claude."""
    headers = {"User-Agent": "BlissNewsletter/1.0 (daily positive newsletter)"}
    for url, extractor in _QUOTE_APIS:
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            resp.raise_for_status()
            return extractor(resp.json())
        except Exception as exc:
            logger.warning("Quote API %s failed: %s", url, exc)
    logger.info("All quote APIs failed; using Claude")
    return _claude_quote()


def _claude_quote() -> dict:
    msg = _claude().messages.create(
        model="claude-opus-4-8",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": (
                "Give me one inspiring or thought-provoking quote suitable for a "
                "daily uplifting newsletter. Reply with ONLY valid JSON in this "
                'exact format: {"quote": "...", "author": "..."}'
            ),
        }],
    )
    text = msg.content[0].text.strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return {"quote": text, "author": "Unknown"}


# ---------------------------------------------------------------------------
# Helpers for RSS + Claude summarization
# ---------------------------------------------------------------------------

def _fetch_feed_entries(feeds: list[str], max_per_feed: int = 5) -> list[dict]:
    entries = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                image = _extract_image(entry)
                entries.append({
                    "title": getattr(entry, "title", ""),
                    "summary": getattr(entry, "summary", ""),
                    "link": getattr(entry, "link", ""),
                    "published": getattr(entry, "published", ""),
                    "image": image,
                })
        except Exception as exc:
            logger.warning("Feed %s failed: %s", url, exc)
    return entries


def _extract_image(entry) -> str:
    """Best-effort image extraction from an RSS entry."""
    # media:thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        return entry.media_thumbnail[0].get("url", "")
    # media:content
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("type", "").startswith("image"):
                return m.get("url", "")
    # enclosures
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("url", "")
    # Parse summary HTML
    summary = getattr(entry, "summary", "") or ""
    soup = BeautifulSoup(summary, "lxml")
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]
    return ""


def _pick_and_summarize(entries: list[dict], section_name: str, system_prompt: str) -> dict:
    """Ask Claude to pick the best entry and write a newsletter blurb."""
    if not entries:
        return _claude_fallback_story(section_name, system_prompt)

    candidates = json.dumps(
        [{"index": i, "title": e["title"], "summary": e["summary"][:400]}
         for i, e in enumerate(entries)],
        indent=2,
    )

    msg = _claude().messages.create(
        model="claude-opus-4-8",
        max_tokens=600,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                f"Here are today's candidate stories:\n{candidates}\n\n"
                "Pick the single most uplifting, positive story and write a "
                "compelling 2-3 sentence newsletter blurb. Reply with ONLY "
                "valid JSON:\n"
                '{"index": <number>, "headline": "...", "blurb": "..."}'
            ),
        }],
    )

    text = msg.content[0].text.strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        chosen = entries[0]
        return {
            "headline": chosen["title"],
            "blurb": BeautifulSoup(chosen["summary"], "lxml").get_text()[:300],
            "link": chosen["link"],
            "image": chosen["image"],
        }

    data = json.loads(json_match.group())
    idx = int(data.get("index", 0))
    chosen = entries[idx] if 0 <= idx < len(entries) else entries[0]
    return {
        "headline": data.get("headline", chosen["title"]),
        "blurb": data.get("blurb", ""),
        "link": chosen["link"],
        "image": chosen["image"],
    }


def _claude_fallback_story(section_name: str, system_prompt: str) -> dict:
    """Generate a plausible story when RSS feeds are unavailable."""
    today = datetime.now().strftime("%B %d, %Y")
    msg = _claude().messages.create(
        model="claude-opus-4-8",
        max_tokens=400,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                f"Write a short, uplifting {section_name} story for {today}. "
                "It should be realistic and fact-based. Reply with ONLY valid JSON:\n"
                '{"headline": "...", "blurb": "...", "link": "", "image": ""}'
            ),
        }],
    )
    text = msg.content[0].text.strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return {"headline": section_name, "blurb": text, "link": "", "image": ""}


# ---------------------------------------------------------------------------
# Good News
# ---------------------------------------------------------------------------

_GOOD_NEWS_SYSTEM = (
    "You are the editor of an uplifting daily newsletter. "
    "You select positive, feel-good news stories that celebrate human kindness, "
    "scientific breakthroughs, community achievements, or environmental wins. "
    "Avoid politics and tragedy. Write in a warm, energetic tone."
)


def fetch_good_news() -> dict:
    entries = _fetch_feed_entries(GOOD_NEWS_FEEDS)
    return _pick_and_summarize(entries, "good news", _GOOD_NEWS_SYSTEM)


# ---------------------------------------------------------------------------
# Impactful AI
# ---------------------------------------------------------------------------

_AI_SYSTEM = (
    "You are the editor of an uplifting daily newsletter. "
    "You select stories about artificial intelligence being used for positive, "
    "beneficial purposes: healthcare, climate, accessibility, education, "
    "scientific discovery, or humanitarian aid. Write in an optimistic, "
    "accessible tone. Avoid hype and focus on real impact."
)


def fetch_ai_impact() -> dict:
    entries = _fetch_feed_entries(AI_IMPACT_FEEDS)
    # Filter to AI-relevant entries only
    ai_keywords = {"ai", "artificial intelligence", "machine learning", "deep learning",
                   "neural", "llm", "model", "robot", "automation", "algorithm"}
    ai_entries = [
        e for e in entries
        if any(kw in (e["title"] + e["summary"]).lower() for kw in ai_keywords)
    ]
    return _pick_and_summarize(ai_entries or entries, "impactful AI", _AI_SYSTEM)
