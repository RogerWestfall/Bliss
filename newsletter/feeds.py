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
            "max_uses": 3,
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
    "Output ONLY a numbered list of up to 4 article URLs, one per line:\n\n"
    "1. https://...\n"
    "2. https://...\n"
    "3. https://...\n"
    "4. https://...\n\n"
    "Rules:\n"
    "- Only articles published in the last 3 days.\n"
    "- Each URL from a different website.\n"
    "- Full article URLs only — not homepages or section pages.\n"
    "- No aggregator sites: goodnewsnetwork.org, positive.news, goodgoodgood.co, "
    "sunnyskyz.com, happiest.media, inspiremore.com, upworthy.com.\n"
    "- Output the bare URLs only — no titles, no descriptions, no extra text.\n"
)


def fetch_news() -> tuple[dict | None, dict | None, dict | None]:
    today = date.today()
    today_str = today.strftime("%B %d, %Y")

    good_prompt = (
        f"Today is {today_str}. Search for news stories from the last 3 days that would make "
        "someone feel genuinely hopeful, proud, or inspired — a scientific discovery, a community "
        "coming together, an underdog winning, a person beating the odds, the environment "
        "recovering, an act of generosity, animals thriving, or the world improving in some way.\n"
        "Prefer: New York Times, Guardian, BBC, NPR, Reuters, AP, The Independent, CNN.\n\n"
        + _URL_RULES
    )

    ai_prompt = (
        f"Today is {today_str}. Search for AI technology news stories from the last 3 days "
        "that would make someone feel excited or hopeful about the future — a new AI tool, "
        "a research breakthrough, or any development showing AI making life better or "
        "expanding what's possible.\n"
        "Prefer: MIT Technology Review, Wired, Nature, The Verge, STAT News, Ars Technica, "
        "TechCrunch, New Scientist, NPR, BBC.\n\n"
        + _URL_RULES
    )

    ny_prompt = (
        f"Today is {today_str}. Search for New York City news stories from the last 3 days "
        "that capture what makes the city feel alive — a neighborhood doing something remarkable, "
        "a local team or person winning, a new restaurant or venue opening, street art, "
        "community pride, or anything distinctly New York.\n"
        "Brooklyn and Manhattan preferred but any NYC borough is fine. "
        "At most 1 sports result. Only things that already happened.\n"
        "Prefer: New York Times, Gothamist, Brooklyn Paper, Bklyner, Hyperallergic, "
        "Curbed NY, Timeout NY, Eater NY, New York Magazine, amNY.\n\n"
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
