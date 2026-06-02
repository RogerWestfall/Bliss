"""Fetch newsletter content via Claude web search + article metadata extraction."""

import logging
import re
from datetime import date

import anthropic
import requests
from lxml import html as lhtml

from newsletter.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlissNewsletter/2.0 (rogerlwestfall@gmail.com)"}
_MODEL = "claude-haiku-4-5-20251001"
_client_instance = None

_URL_RE = re.compile(r'https?://[^\s<>"()\[\]{}]+')


def _client() -> anthropic.Anthropic:
    global _client_instance
    if _client_instance is None:
        _client_instance = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client_instance


# ── Article metadata ──────────────────────────────────────────────────────────

def _fetch_meta(url: str) -> dict:
    """Fetch og:title, og:description, og:image from an article URL."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = lhtml.fromstring(resp.content)

        title = ""
        for xpath, attr in [
            ('.//meta[@property="og:title"]', "content"),
            ('.//meta[@name="twitter:title"]', "content"),
        ]:
            el = root.find(xpath)
            if el is not None:
                title = el.get(attr, "").strip()
                if title:
                    break
        if not title:
            el = root.find('.//title')
            if el is not None and el.text:
                title = el.text.strip()

        description = ""
        for xpath, attr in [
            ('.//meta[@property="og:description"]', "content"),
            ('.//meta[@name="description"]', "content"),
        ]:
            el = root.find(xpath)
            if el is not None:
                description = el.get(attr, "").strip()
                if description:
                    break

        image = ""
        for xpath, attr in [
            ('.//meta[@property="og:image"]', "content"),
            ('.//meta[@name="twitter:image"]', "content"),
        ]:
            el = root.find(xpath)
            if el is not None:
                src = el.get(attr, "")
                if src.startswith("http"):
                    image = src
                    break

        return {"title": title, "description": description, "image": image}
    except Exception as exc:
        logger.debug("fetch_meta failed for %s: %s", url, exc)
        return {"title": "", "description": "", "image": ""}


# ── URL parsing ───────────────────────────────────────────────────────────────

def _parse_urls(text: str) -> list[str]:
    """Extract up to 4 URLs from the model's response."""
    urls = []
    seen_domains: set = set()

    for url in _URL_RE.findall(text):
        url = url.rstrip('.,;)')
        if not url.startswith("http"):
            continue
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            # Require a real path (not bare domain)
            if len(parsed.path.rstrip("/")) == 0:
                continue
            domain = parsed.netloc.removeprefix("www.")
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            urls.append(url)
        except Exception:
            continue
        if len(urls) == 4:
            break

    return urls


# ── Search ────────────────────────────────────────────────────────────────────

def _search_section(prompt: str) -> str:
    """One focused web search call. Returns the model's raw text response."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=1000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    block_types = [getattr(b, "type", "?") for b in resp.content]
    logger.info("stop_reason=%s | blocks=%s", resp.stop_reason, block_types)
    text = "".join(
        getattr(b, "text", "") or ""
        for b in resp.content
        if getattr(b, "type", "") == "text"
    )
    logger.info("Response (%d chars): %s", len(text), text[:500])
    return text


def _fetch_section(prompt: str) -> dict | None:
    """Search for stories, parse URLs, fetch metadata, return shaped section."""
    text = _search_section(prompt)
    urls = _parse_urls(text)
    logger.info("URLs found: %s", urls)

    if not urls:
        return None

    # Fetch metadata for all URLs; find the first with an image for featured
    articles = []
    for url in urls:
        meta = _fetch_meta(url)
        articles.append({
            "headline": meta["title"],
            "blurb": meta["description"],
            "link": url,
            "image": meta["image"],
        })
        logger.info("  %s → title=%r image=%s", url[:60], meta["title"][:50] if meta["title"] else "", bool(meta["image"]))

    # Drop articles where we couldn't get a headline
    articles = [a for a in articles if a["headline"]]
    if not articles:
        return None

    # Pick the first article with an image as featured; fall back to first
    featured_idx = next((i for i, a in enumerate(articles) if a["image"]), 0)
    main = articles[featured_idx]
    rest = [a for i, a in enumerate(articles) if i != featured_idx]

    return {
        "headline": main["headline"],
        "blurb": main["blurb"],
        "link": main["link"],
        "image": main["image"],
        "more": [{"headline": a["headline"], "link": a["link"]} for a in rest],
    }


# ── Quote of the Day ─────────────────────────────────────────────────────────

_FALLBACK_QUOTE = {
    "quote": "Keep your face always toward the sunshine, and shadows will fall behind you.",
    "author": "Walt Whitman",
}


def fetch_quote() -> dict:
    try:
        resp = requests.get(
            "https://zenquotes.io/api/random", headers=_HEADERS, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()[0]
        return {"quote": data["q"], "author": data["a"]}
    except Exception as exc:
        logger.warning("ZenQuotes failed (%s) — using fallback", exc)
        return _FALLBACK_QUOTE


# ── News ──────────────────────────────────────────────────────────────────────

_URL_RULES = (
    "Output ONLY a numbered list of 1-4 URLs, one per line:\n\n"
    "1. https://...\n"
    "2. https://...\n"
    "3. https://...\n"
    "4. https://...\n\n"
    "Rules:\n"
    "- Prefer articles from the last 7 days, but older is fine if very relevant.\n"
    "- Each URL from a different website.\n"
    "- No aggregator-only sites: goodnewsnetwork.org, positive.news, goodgoodgood.co, "
    "sunnyskyz.com, happiest.media, inspiremore.com, upworthy.com.\n"
    "- CRITICAL: Always output at least 1 URL. Never explain why you couldn't find "
    "results — just output the best URLs you found, even if imperfect.\n"
    "- Output the bare URLs only — no titles, no descriptions, no extra text.\n"
)


def fetch_news() -> tuple[dict | None, dict | None, dict | None]:
    today = date.today()
    today_str = today.strftime("%B %d, %Y")

    good_prompt = (
        f"Today is {today_str}. Search for news and stories from the last 3 days with a "
        "positive, uplifting, or feel-good tone. Case very broadly — anything qualifies as long "
        "as it doesn't bring negativity. Examples of what works:\n"
        "- Good news: breakthroughs, rescues, records broken, problems solved\n"
        "- Inspiring: underdogs winning, people beating the odds, acts of generosity\n"
        "- Nostalgic: comebacks, anniversaries, reunions, beloved things returning\n"
        "- Joyful: animals being cute or thriving, kids doing something amazing, "
        "unexpected kindness, communities celebrating\n"
        "- Interesting & uplifting: fascinating discoveries, hidden histories uncovered, "
        "beautiful places, surprising talents, creative achievements\n"
        "- Feel-good culture: a beloved show returning, a classic getting recognized, "
        "an artist having a moment, sports joy\n"
        "Prefer: New York Times, Guardian, BBC, NPR, Reuters, AP, The Independent, CNN, "
        "The Atlantic, Smithsonian, National Geographic.\n\n"
        + _URL_RULES
    )

    ai_prompt = (
        f"Today is {today_str}. Search for AI and technology stories from the last 3 days "
        "with a positive, exciting, or fascinating angle. Cast broadly — anything qualifies "
        "as long as it doesn't bring negativity. Examples of what works:\n"
        "- New AI tools, apps, or products that are impressive or useful\n"
        "- Research breakthroughs in AI, robotics, or science\n"
        "- Technology solving real problems or helping people\n"
        "- Fascinating tech discoveries or unexpected applications\n"
        "- AI in art, music, creativity, or culture\n"
        "- Interesting or surprising things AI can now do\n"
        "- Feel-good tech stories: accessibility, education, healthcare wins\n"
        "Prefer: MIT Technology Review, Wired, Nature, The Verge, STAT News, Ars Technica, "
        "TechCrunch, New Scientist, NPR, BBC, Scientific American.\n\n"
        + _URL_RULES
    )

    ny_prompt = (
        f"Today is {today_str}. Search for New York City stories from the last 3 days "
        "with a positive, uplifting, nostalgic, or feel-good angle. Cast broadly — anything "
        "qualifies as long as it doesn't bring negativity. Examples of what works:\n"
        "- Neighborhood life, local openings, community moments\n"
        "- NYC sports wins or feel-good sports stories\n"
        "- Street art, murals, culture, music, food\n"
        "- A beloved NYC institution celebrating a milestone\n"
        "- Hidden histories or fascinating facts about the city\n"
        "- Only-in-New-York moments, characters, or stories\n"
        "- Nostalgic NYC content: things returning, anniversaries, throwbacks\n"
        "Brooklyn and Manhattan preferred but any NYC borough is fine.\n"
        "Prefer: New York Times, Gothamist, Brooklyn Paper, Bklyner, Hyperallergic, "
        "Curbed NY, Timeout NY, Eater NY, New York Magazine, amNY, Patch NYC.\n\n"
        + _URL_RULES
    )

    try:
        logger.info("Searching: Good News...")
        good_news = _fetch_section(good_prompt)

        logger.info("Searching: AI Impact...")
        ai_impact = _fetch_section(ai_prompt)

        logger.info("Searching: New York...")
        ny_news = _fetch_section(ny_prompt)

        logger.info("Sections — good: %s | ai: %s | ny: %s",
                    good_news["headline"][:50] if good_news else "OMITTED",
                    ai_impact["headline"][:50] if ai_impact else "OMITTED",
                    ny_news["headline"][:50] if ny_news else "OMITTED")
        return good_news, ai_impact, ny_news

    except Exception:
        logger.exception("fetch_news failed")
        return None, None, None
